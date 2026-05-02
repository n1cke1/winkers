"""Coupling aggregator — detects cross-file links from hardcoded_artifacts.

After description-author runs over every unit (function or template
section), each unit carries a list of `hardcoded_artifacts`: values
that, if changed in the canonical source, require synchronized changes
elsewhere. This module finds shared values across units and emits
proposed traceability_units.

Inverts the detection direction: instead of one project-wide LLM pass
scanning everything for couplings, every unit self-reports its anchors
and a deterministic aggregator joins them. Cost: O(units × artifacts)
hash insertions; no extra LLM calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from winkers.descriptions.models import HardcodedArtifact

log = logging.getLogger(__name__)

# A pure numeric without context: "2", "-1", "0.5", etc.
_BARE_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$|^-?\.\d+$")

# Values too generic to be coupling signal on their own. The
# description-author prompt already discourages emitting these, but
# defensive filtering catches LLM lapses.
_GENERIC_VALUES = {
    "0", "1", "0.0", "1.0", "-1",
    "True", "False", "None", "null",
    "", "id", "name", "type", "value", "key",
}


@dataclass
class ArtifactHit:
    """One occurrence of a canonical value in a specific unit."""
    unit_id: str
    unit_kind: str       # "function_unit" | "traceability_unit"
    file: str
    artifact: HardcodedArtifact


@dataclass
class ProposedCoupling:
    """A canonical value shared across multiple units in different files.

    The aggregator emits one of these per unique value cluster that
    crosses file boundaries; the consumer (CLI / pipeline) decides
    whether to materialize it as a traceability_unit.
    """
    canonical_value: str
    primary_kind: str             # most common ArtifactKind in the cluster
    hits: list[ArtifactHit] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len({h.file for h in self.hits})

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def kind_uniformity(self) -> float:
        """Fraction of hits sharing the primary kind (1.0 = all same kind).

        Mixed-kind clusters often signal coincidental matches (e.g. the
        number "33" used as a count in one place and a route id in
        another) rather than real couplings.
        """
        if not self.hits:
            return 0.0
        same = sum(1 for h in self.hits if h.artifact.kind == self.primary_kind)
        return same / len(self.hits)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _unit_files(unit: dict) -> list[str]:
    """Best-effort: collect file paths a unit is anchored to.

    function_unit → anchor.file (single).
    traceability_unit → source_files (may have several).
    """
    if unit.get("kind") == "function_unit":
        anchor = unit.get("anchor") or {}
        f = anchor.get("file")
        return [f] if f else []
    return list(unit.get("source_files", []))


def _is_bare_numeric_artifact(raw: dict) -> bool:
    """True if artifact is a context-less plain number — likely false-positive material.

    A value of "2" extracted from one function as `count of fixed columns`
    and from another as `convergence tolerance in кДж/кг` shouldn't cluster:
    both extractions are correct, but the values are coincidental, not coupling.

    Bare numbers without `surface` text (LLM didn't capture an inline phrase
    like "33 переменных") get the strictest filter — these are the noisy ones.
    Numbers WITH surface text indicate the LLM saw a load-bearing inline
    counter and stay in the index.

    Identifier lists, route paths, named identifiers, phrases — never bare
    numerics; never filtered by this rule.
    """
    kind = raw.get("kind")
    if kind not in ("count", "threshold", "other"):
        return False
    val = raw.get("value")
    if not isinstance(val, str):
        return False
    if not _BARE_NUMERIC_RE.match(val):
        return False
    surface = (raw.get("surface") or "").strip()
    # `surface == value` (e.g. "2" == "2") doesn't count as meaningful context.
    if surface and surface != val:
        return False
    return True


def _canonical_value(raw: dict) -> str:
    """Stable string key for cross-unit equality.

    Identifier lists are sorted then JSON-serialized; everything else
    becomes its own string. This mirrors `HardcodedArtifact.canonical_key`
    but accepts plain dicts (from already-saved units.json).
    """
    val = raw.get("value")
    if isinstance(val, list):
        return json.dumps(sorted(val), ensure_ascii=False)
    if val is None:
        return ""
    return str(val)


def detect_couplings(
    units: list[dict],
    min_hits: int = 2,
    min_files: int = 2,
) -> list[ProposedCoupling]:
    """Find values shared across multiple units in different files.

    `min_files=2` is the key invariant — an artifact repeated within one
    file isn't a *cross-file* coupling. Within-file repeats happen when
    a function and a constant block in the same module share a name; not
    interesting for traceability.
    """
    inverted: dict[str, list[ArtifactHit]] = defaultdict(list)

    for unit in units:
        files = _unit_files(unit)
        if not files:
            continue
        # We index against the unit's primary file — most artifacts originate
        # at one canonical home. For traceability_units that span multiple
        # source files, use the first; consumers are tracked separately.
        primary_file = files[0]

        for raw in unit.get("hardcoded_artifacts", []):
            value = _canonical_value(raw)
            if not value or value in _GENERIC_VALUES:
                continue
            if _is_bare_numeric_artifact(raw):
                # Skip context-less integers — `2`, `6`, `100` etc. cluster
                # falsely across unrelated domains (column counts vs
                # tolerances). Numbers with surface text remain.
                continue
            try:
                art = HardcodedArtifact.model_validate(raw)
            except Exception as e:
                log.debug("skipping malformed artifact in %s: %s",
                          unit.get("id"), e)
                continue
            inverted[value].append(ArtifactHit(
                unit_id=unit["id"],
                unit_kind=unit.get("kind", "unknown"),
                file=primary_file,
                artifact=art,
            ))

    couplings: list[ProposedCoupling] = []
    for value, hits in inverted.items():
        if len(hits) < min_hits:
            continue
        if len({h.file for h in hits}) < min_files:
            continue
        # Most-common kind drives the cluster's classification.
        kind_counts = defaultdict(int)
        for h in hits:
            kind_counts[h.artifact.kind] += 1
        primary_kind = max(kind_counts, key=kind_counts.get)
        couplings.append(ProposedCoupling(
            canonical_value=value,
            primary_kind=primary_kind,
            hits=hits,
        ))

    # Most cross-cutting first; tie-break by total hit count.
    couplings.sort(
        key=lambda c: (c.file_count, c.hit_count),
        reverse=True,
    )
    return couplings


# ---------------------------------------------------------------------------
# Conversion to traceability_unit
# ---------------------------------------------------------------------------

def proposed_to_unit(coupling: ProposedCoupling) -> dict:
    """Render a ProposedCoupling as a traceability_unit dict.

    The id is stable (deterministic hash of value + member unit ids), so
    re-running detection on the same data produces the same id — important
    for incremental embedding.
    """
    member_ids = sorted({h.unit_id for h in coupling.hits})
    digest_input = f"{coupling.canonical_value}|{','.join(member_ids)}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:8]

    display_value = coupling.canonical_value
    if len(display_value) > 40:
        display_value = display_value[:37] + "..."

    files_summary = ", ".join(sorted({h.file for h in coupling.hits})[:4])
    # Description is in English to match the rest of the units index
    # (Issue 2 — monolingual EN embedding space). Russian-language
    # queries reach this unit through the pre-session prompt
    # translation in `descriptions/translator`, not through bilingual
    # description text.
    description = (
        f"Cross-file coupling on the {coupling.primary_kind} value "
        f"{coupling.canonical_value!r}: appears in {coupling.hit_count} "
        f"unit(s) across {coupling.file_count} file(s) ({files_summary}). "
        "Changing the canonical source requires a synchronous edit at "
        "every consumer site. Search terms: \"cross-file coupling\", "
        "\"hardcoded value coupling\", \"cross-cutting constraint\". "
        "Auto-detected by the aggregator from `hardcoded_artifacts` "
        "extracted from unit descriptions — consumer contexts may "
        "diverge from the canonical meaning, so verify intent before "
        "syncing each call site."
    )

    consumers = []
    for h in coupling.hits:
        consumers.append({
            "file": h.file,
            "anchor": h.unit_id,
            "what_to_check": h.artifact.context,
            "surface": h.artifact.surface or h.artifact.context,
        })

    return {
        "id": f"coupling:{coupling.primary_kind}:{digest}",
        "kind": "traceability_unit",
        "name": f"Coupling: {coupling.primary_kind} {display_value!r}",
        "source_files": sorted({h.file for h in coupling.hits}),
        "source_anchors": member_ids,
        "description": description,
        "hardcoded_artifacts": [],
        "consumers": consumers,
        "meta": {
            "origin": "auto-detected",
            "canonical_value": coupling.canonical_value,
            "primary_kind": coupling.primary_kind,
            "hit_count": coupling.hit_count,
            "file_count": coupling.file_count,
            "kind_uniformity": round(coupling.kind_uniformity, 2),
        },
    }
