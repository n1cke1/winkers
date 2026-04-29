"""Units store — persistence + staleness detection for `.winkers/units.json`.

Single JSON file, list of unit dicts. The store knows how to:

- Load / save the file (graceful on missing or malformed input).
- Spot stale function_units by comparing their stored `source_hash`
  against the current graph's `FunctionNode.ast_hash`.
- Spot stale template_section units by re-scanning the template and
  hashing the section content.
- Upsert by id (no diffs at this layer; that's the embedding builder's
  job downstream).

The store does NOT decide *how* to refresh stale units (that's the
description-author runner). It only identifies what needs work.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

UNITS_FILENAME = "units.json"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class UnitsStore:
    """Wraps `.winkers/units.json` with load/save + staleness queries."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / ".winkers" / UNITS_FILENAME

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> list[dict]:
        """Read units list from disk; return [] if file missing or invalid.

        Malformed JSON is logged and treated as empty — better than
        crashing init when a stale or hand-edited file is unparseable.
        """
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.warning("units.json malformed (%s); treating as empty", e)
            return []
        if not isinstance(data, dict):
            return []
        units = data.get("units", [])
        if not isinstance(units, list):
            return []
        return units

    def save(self, units: list[dict]) -> None:
        """Write units list atomically.

        Stable ordering: id-sorted so diffs across init runs stay readable.
        Preserves any extra top-level keys (`impact_meta`, future
        sections) that other callers may have stored in the same file.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sorted_units = sorted(units, key=lambda u: u.get("id", ""))

        existing_top = self._load_top_level()
        existing_top["units"] = sorted_units

        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(existing_top, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    # Top-level helpers (Wave 4d — impact_meta lives next to `units`)
    # ------------------------------------------------------------------

    def _load_top_level(self) -> dict:
        """Read the raw top-level dict from units.json — empty on miss/parse-fail."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def load_impact_meta(self) -> dict:
        """Return the persisted impact_meta dict (or {} if absent)."""
        meta = self._load_top_level().get("impact_meta", {})
        return meta if isinstance(meta, dict) else {}

    def save_impact_meta(self, meta: dict) -> None:
        """Update only the `impact_meta` section, preserving units."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_top = self._load_top_level()
        existing_top["impact_meta"] = meta
        if "units" not in existing_top:
            existing_top["units"] = []
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(existing_top, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def upsert(self, units: list[dict], new_unit: dict) -> list[dict]:
        """Return a new list with `new_unit` replacing any same-id entry.

        Pure function — caller is responsible for saving. Lets callers
        batch upserts before paying for a disk write.
        """
        uid = new_unit.get("id")
        if not uid:
            return units
        return [u for u in units if u.get("id") != uid] + [new_unit]

    # ------------------------------------------------------------------
    # Staleness — function_unit
    # ------------------------------------------------------------------

    def stale_function_units(
        self,
        existing_units: list[dict],
        graph_functions: dict,
    ) -> set[str]:
        """Return fn_ids that need (re-)description.

        A function_unit is stale if any of:
        - it's missing from existing_units (never described)
        - its `source_hash` doesn't match the current FunctionNode.ast_hash
        - the function exists in the graph but the unit's source_hash is None
          (older unit version without hash → re-describe to upgrade)

        Functions with `ast_hash == None` in the graph are skipped — we
        can't detect changes without the hash, so we don't churn.
        """
        existing_by_id = {
            u["id"]: u for u in existing_units
            if u.get("kind") == "function_unit"
        }
        stale: set[str] = set()
        for fn_id, fn in graph_functions.items():
            ast_hash = fn.get("ast_hash") if isinstance(fn, dict) else getattr(fn, "ast_hash", None)
            if ast_hash is None:
                continue  # graph didn't compute a hash — can't compare
            existing = existing_by_id.get(fn_id)
            if existing is None:
                stale.add(fn_id)
                continue
            stored = existing.get("source_hash")
            if stored != ast_hash:
                stale.add(fn_id)
        return stale

    # ------------------------------------------------------------------
    # Staleness — template_unit
    # ------------------------------------------------------------------

    def stale_data_file_units(
        self,
        existing_units: list[dict],
        data_files: list,
    ) -> set[str]:
        """Return ids of data files that need (re-)description.

        Compares stored `source_hash` against `_content_hash(file_content)`.
        New files are stale (no stored hash). Removed files aren't
        reported here — orphan pruning handles those.

        `data_files` — list of DataFileEntry-like objects (we duck-type
        on `.rel_path` and `.content`).
        """
        existing_by_id = {
            u["id"]: u for u in existing_units
            if u.get("id", "").startswith("data:")
        }
        stale: set[str] = set()
        for entry in data_files:
            uid = f"data:{entry.rel_path}"
            current_hash = _content_hash(entry.content)
            existing = existing_by_id.get(uid)
            if existing is None:
                stale.add(uid)
                continue
            if existing.get("source_hash") != current_hash:
                stale.add(uid)
        return stale

    def stale_template_units(
        self,
        existing_units: list[dict],
        sections: list,
    ) -> set[str]:
        """Return ids of template sections that need (re-)description.

        Compares stored `source_hash` against `_content_hash(section.content)`.
        New sections are stale (no stored hash). Removed sections aren't
        reported here — orphan pruning is the caller's concern.

        `sections` is a list of TemplateSection (from
        winkers.templates.scanner) — we duck-type on `.id` and `.content`.
        """
        # Index existing template units by their canonical id
        # (`template:<file>#<section_id>`).
        existing_by_id = {
            u["id"]: u for u in existing_units
            if u.get("id", "").startswith("template:")
        }
        stale: set[str] = set()
        for sec in sections:
            uid = f"template:{sec.file}#{sec.id}"
            current_hash = _content_hash(sec.content)
            existing = existing_by_id.get(uid)
            if existing is None:
                stale.add(uid)
                continue
            if existing.get("source_hash") != current_hash:
                stale.add(uid)
        return stale

    # ------------------------------------------------------------------
    # Orphan pruning
    # ------------------------------------------------------------------

    def prune_orphans(
        self,
        units: list[dict],
        live_function_ids: set[str],
        live_template_ids: set[str],
        live_data_ids: set[str] | None = None,
        live_value_ids: set[str] | None = None,
        live_class_ids: set[str] | None = None,
        live_attr_ids: set[str] | None = None,
    ) -> list[dict]:
        """Drop units whose anchor target no longer exists.

        - function_unit: keep iff its id is in `live_function_ids`.
        - template_unit (`template:` prefix): keep iff in `live_template_ids`.
        - data_file_unit (`data:` prefix): keep iff in `live_data_ids`.
          (`live_data_ids=None` means "don't prune data units" — used
          when the caller hasn't scanned data files; preserves backward
          compatibility for tests not covering data file flow.)
        - value_unit (`value:` prefix): keep iff in `live_value_ids`.
          `None` means "don't prune" — used when callers haven't run
          the value_locked detector.
        - class_unit (`class:` prefix): keep iff in `live_class_ids`.
          `None` means "don't prune".
        - attribute_unit (`attr:` prefix): keep iff in `live_attr_ids`.
          `None` means "don't prune".
        - everything else (manual traceability, auto-detected couplings):
          kept untouched — couplings are regenerated wholesale by the aggregator.
        """
        kept: list[dict] = []
        dropped = 0
        for u in units:
            uid = u.get("id", "")
            kind = u.get("kind")
            if kind == "function_unit":
                if uid in live_function_ids:
                    kept.append(u)
                else:
                    dropped += 1
                continue
            if uid.startswith("template:"):
                if uid in live_template_ids:
                    kept.append(u)
                else:
                    dropped += 1
                continue
            if uid.startswith("data:"):
                if live_data_ids is None or uid in live_data_ids:
                    kept.append(u)
                else:
                    dropped += 1
                continue
            if uid.startswith("value:"):
                if live_value_ids is None or uid in live_value_ids:
                    kept.append(u)
                else:
                    dropped += 1
                continue
            if uid.startswith("class:"):
                if live_class_ids is None or uid in live_class_ids:
                    kept.append(u)
                else:
                    dropped += 1
                continue
            if uid.startswith("attr:"):
                if live_attr_ids is None or uid in live_attr_ids:
                    kept.append(u)
                else:
                    dropped += 1
                continue
            kept.append(u)
        if dropped:
            log.info("Pruned %d orphan unit(s)", dropped)
        return kept


# ---------------------------------------------------------------------------
# Utility — surface content hash to callers (description authoring writes
# this back into the unit so subsequent staleness checks work).
# ---------------------------------------------------------------------------

def section_hash(content: str) -> str:
    """Public wrapper for the content-hash function used internally."""
    return _content_hash(content)


def data_file_hash(content: str) -> str:
    """Hash data-file content for staleness detection.

    Same algorithm as section_hash — separate name documents intent
    at call sites.
    """
    return _content_hash(content)
