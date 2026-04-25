"""Build the unified semantic-units file for the CHP-project spike.

Inputs:
  - units_manual.py    -- 27 hand-authored units (20 Python + 7 JS UI tabs)
  - CHP/data/ui_traceability.json -- 12 concept entries (MILP vars,
    coefficients, results structure, etc.)

Output:
  - scripts/units.json -- 39 units in the unified schema, ready to embed.

The traceability_unit conversion strips line numbers from `location` fields
and replaces them with semantic anchors (function names, JSON paths, section
headings). For the rare cases where a regex pattern is the only stable
locator, it goes into the `pattern` field.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from units_manual import ALL_MANUAL_UNITS, TRACEABILITY_DESCRIPTION_OVERRIDES  # noqa: E402

CHP_ROOT = Path("C:/Development/CHP model web")
TRACEABILITY_JSON = CHP_ROOT / "data/ui_traceability.json"
OUTPUT = Path(__file__).parent / "units.json"


# ---------------------------------------------------------------------------
# Anchor extraction: turn raw "location" prose from ui_traceability.json
# into structured (anchor, pattern) pairs.
# ---------------------------------------------------------------------------

# Patterns we know reliably appear in CHP traceability locations.
_FN_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\(\)")
_LINE_RE = re.compile(r"строки?\s*\d+(?:[-–]\d+)?|line[s]?\s*\d+|≈\s*стр\w*\s*\d+|≈\s*\d+")
_SECTION_RE = re.compile(r"шаг[\s_]*\d+|секци[яи]\s*\d+|карточк[аи]\s*коэффициентов|вкладк[аи]\s+[А-ЯA-Z]\w*")
_JSON_PATH_HINT_RE = re.compile(r"(coefficients|variables|optimizer_balance|RESULT_SECTIONS|TURBINE_SPECS|IDX|N\b)")


def derive_anchor(file: str, location: str) -> tuple[str, str]:
    """Pick a stable anchor and (if needed) regex pattern for a consumer ref.

    Strategy: drop any line-number prose, prefer function name from `location`,
    fall back to known JSON/JS identifier hints, then to section names.
    Returns (anchor, pattern) — pattern is empty when anchor is sufficient.
    """
    loc_clean = _LINE_RE.sub("", location).strip(" ()—–-,;")

    # Drop bare 'строки X-Y' prefixes
    loc_clean = re.sub(r"^\(?\s*$", "", loc_clean)

    # 1) Function call mentioned in location
    fn_match = _FN_RE.search(loc_clean)
    if fn_match:
        return fn_match.group(0), ""

    # 2) Known constants / JSON keys
    json_match = _JSON_PATH_HINT_RE.search(loc_clean)
    if json_match:
        return json_match.group(1), ""

    # 3) UI section heading
    sec_match = _SECTION_RE.search(loc_clean)
    if sec_match:
        return sec_match.group(0), ""

    # 4) For files in /api/* — use the route or an identifier in location
    if not loc_clean:
        return "", ""

    # Last resort: use the prose minus line numbers as anchor
    return loc_clean[:80], ""


# ---------------------------------------------------------------------------
# Description synthesis for traceability_unit (no LLM, deterministic template)
# ---------------------------------------------------------------------------

def synthesize_description(entry: dict) -> str:
    """Build a 60-120 word description from structured fields.

    Combines: name + source_anchors (preferred) or source_files +
    condensed current_state + first consumer's what_to_check.
    """
    name = entry.get("name", entry.get("id", ""))
    source_files = entry.get("source_files", [])
    source_anchors = entry.get("source_anchors", [])
    current_state = entry.get("current_state", {})
    consumers = entry.get("consumers", [])

    # 1. Lead sentence — prefer fn-level anchors when present
    if source_anchors:
        anchor_phrases = [f"`{a.split('::')[1]}()` в `{a.split('::')[0]}`"
                          for a in source_anchors[:2]]
        canon = " и ".join(anchor_phrases)
    elif source_files:
        canon = ", ".join(f"`{f}`" for f in source_files[:2])
    else:
        canon = "нескольких местах кодовой базы"
    parts = [f"Описывает «{name}» — концепт, чьё каноническое определение живёт в {canon}."]

    # 2. Condensed current_state
    state_summary = _condense_current_state(current_state)
    if state_summary:
        parts.append(state_summary)

    # 3. Cross-cutting impact (synonym list — поможет embedding'у)
    parts.append(
        "Это «traceability concept», «cross-cutting сущность», «сквозной артефакт» — "
        "изменение в каноне требует синхронной правки нескольких consumer-файлов."
    )

    # 4. First consumer's what_to_check as a concrete hint
    if consumers:
        first_check = consumers[0].get("what_to_check", "").strip()
        if first_check:
            # Trim long checks
            if len(first_check) > 220:
                first_check = first_check[:217] + "…"
            parts.append("Главный consumer: " + first_check)

    return " ".join(parts)


def _condense_current_state(state: dict) -> str:
    """Convert structured `current_state` into one descriptive sentence."""
    if not state:
        return ""
    fragments = []
    for key, value in state.items():
        if key.startswith("_"):
            continue
        if isinstance(value, list):
            sample = ", ".join(str(v) for v in value[:5])
            fragments.append(f"{key}: {sample}{'…' if len(value) > 5 else ''}")
        elif isinstance(value, dict):
            keys_sample = ", ".join(list(value.keys())[:6])
            fragments.append(f"{key}: {keys_sample}{'…' if len(value) > 6 else ''}")
        elif isinstance(value, (str, int, float)):
            v = str(value)
            if len(v) > 100:
                v = v[:97] + "…"
            fragments.append(f"{key}={v}")
    if not fragments:
        return ""
    text = "Текущее состояние: " + "; ".join(fragments[:4])
    if len(text) > 380:
        text = text[:377] + "…"
    return text + "."


# ---------------------------------------------------------------------------
# Convert ui_traceability.json entry → traceability_unit
# ---------------------------------------------------------------------------

def _clean_source_files(raw: list[str]) -> list[str]:
    """Strip arrow suffixes, parens, line refs from source_files entries."""
    cleaned = []
    for sf in raw:
        if "→" in sf:
            sf = sf.split("→")[0]
        if "(" in sf:
            sf = sf.split("(")[0]
        sf = sf.strip().rstrip(",")
        if sf:
            cleaned.append(sf)
    return cleaned


_SRC_FN_RE = re.compile(r"([a-zA-Z_][a-zA-Z_0-9]*)\(")


def _extract_source_anchors(raw: list[str]) -> list[str]:
    """Pull `file::fn` anchors from raw source_files like
    'engine/chp_model.py → solve_design(), _cond_violations()'.

    Only function-like patterns (`name(`) yield anchors. Constants, class
    names, and route strings are ignored — they're not graph fn_ids and
    thus don't enrich line lookups. Caller can resolve them as plain
    file references.
    """
    anchors = []
    for sf in raw:
        if "→" not in sf:
            continue
        file_part, _, suffix = sf.partition("→")
        file = file_part.strip().rstrip(",")
        if not file:
            continue
        for m in _SRC_FN_RE.finditer(suffix):
            fn = m.group(1)
            # Skip stop-words that look like fns but aren't (rare; defensive).
            if fn.lower() in ("if", "for", "while"):
                continue
            anchors.append(f"{file}::{fn}")
    return anchors


def convert_traceability_entry(entry: dict) -> dict:
    consumers = []
    for c in entry.get("consumers", []):
        file = c.get("file", "")
        # Strip the "→ fn_name" suffix that often appears
        if "→" in file:
            file_part, _, fn_hint = file.partition("→")
            file = file_part.strip()
            location_combined = (fn_hint.strip() + " " + c.get("location", "")).strip()
        else:
            location_combined = c.get("location", "")

        anchor, pattern = derive_anchor(file, location_combined)
        ref = {
            "file": file,
            "anchor": anchor,
            "what_to_check": c.get("what_to_check", "").strip(),
        }
        if pattern:
            ref["pattern"] = pattern
        consumers.append(ref)

    source_files = _clean_source_files(entry.get("source_files", []))
    source_anchors = _extract_source_anchors(entry.get("source_files", []))

    # Manual override beats synthesized text — used to bridge lexical gap
    # observed in bench (T-FC3170, T-132CDB).
    override = TRACEABILITY_DESCRIPTION_OVERRIDES.get(entry["id"])
    if override:
        description = override
    else:
        cleaned_entry = dict(entry)
        cleaned_entry["source_files"] = source_files
        cleaned_entry["source_anchors"] = source_anchors
        description = synthesize_description(cleaned_entry)

    return {
        "id": entry["id"],
        "kind": "traceability_unit",
        "name": entry.get("name", entry["id"]),
        "source_files": source_files,
        "source_anchors": source_anchors,
        "description": description,
        "consumers": consumers,
        "meta": {
            "origin": "ui_traceability.json",
            "captured_status": next(
                (c.get("status", "") for c in entry.get("consumers", []) if c.get("status")),
                "",
            ),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    raw = json.loads(TRACEABILITY_JSON.read_text(encoding="utf-8"))
    converted = [convert_traceability_entry(e) for e in raw["entities"]]

    all_units = list(ALL_MANUAL_UNITS) + converted

    # Embed text = name + description (gives embeddings a stronger signal)
    for u in all_units:
        u["embed_text"] = f"{u.get('name', '')}\n\n{u.get('description', '')}".strip()

    OUTPUT.write_text(
        json.dumps({"units": all_units}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    by_kind = {}
    for u in all_units:
        by_kind[u["kind"]] = by_kind.get(u["kind"], 0) + 1

    print(f"Wrote {OUTPUT}")
    print(f"Total units: {len(all_units)}")
    for k, n in by_kind.items():
        print(f"  {k:20s} {n}")
    print()
    print("Sample traceability_unit (first one converted from JSON):")
    sample = converted[0]
    print(f"  id:          {sample['id']}")
    print(f"  name:        {sample['name']}")
    print(f"  source:      {sample['source_files']}")
    print(f"  consumers:   {len(sample['consumers'])}")
    for c in sample["consumers"][:3]:
        print(f"    - {c['file']}  ({c['anchor']!r})")
        print(f"      check: {c['what_to_check'][:90]}…")
    print()
    print("Description preview:")
    print(f"  {sample['description'][:300]}…")


if __name__ == "__main__":
    main()
