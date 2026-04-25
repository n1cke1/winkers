"""Quick QA pass over scripts/units.json — spot-check description quality."""
import json
import re
from pathlib import Path

UNITS = json.loads((Path(__file__).parent / "units.json").read_text(encoding="utf-8"))["units"]

# Heuristics for "smelly" descriptions
LINE_NUMBER_RE = re.compile(r"строки?\s+\d+|≈\s*стр\w*\s*\d+|line\s*\d+|строка\s+\d+")
CONSUMER_LINE_RE = re.compile(r"\d{2,4}-\d{2,4}|≈\s*\d+|строки?\s*\d+")

print("=== Описаний с упоминанием 'строки N' ===")
for u in UNITS:
    if LINE_NUMBER_RE.search(u["description"]):
        print(f"  {u['id']}: {LINE_NUMBER_RE.search(u['description']).group()!r}")

print()
print("=== Consumer anchors с line-numbers (нежелательно) ===")
hits = 0
for u in UNITS:
    for c in u.get("consumers", []):
        anchor = c.get("anchor", "")
        if CONSUMER_LINE_RE.search(anchor):
            print(f"  {u['id']} → {c['file']}: anchor={anchor!r}")
            hits += 1
print(f"  Total: {hits}")

print()
print("=== Длина description (слов) ===")
lengths = [(u["id"], len(u["description"].split())) for u in UNITS]
lengths.sort(key=lambda x: x[1])
print("  Min 5:")
for i, n in lengths[:5]:
    print(f"    {n:3d}  {i}")
print("  Max 5:")
for i, n in lengths[-5:]:
    print(f"    {n:3d}  {i}")

print()
print("=== Все 19 traceability units (id, source_files, consumers count) ===")
for u in UNITS:
    if u["kind"] != "traceability_unit":
        continue
    src = ", ".join(u.get("source_files", [])[:2])
    print(f"  {u['id']:30s}  src={src:50s}  consumers={len(u.get('consumers', []))}")
