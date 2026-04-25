"""Inspect freshly-indexed data units + their couplings on CHP."""
import json
from pathlib import Path

units = json.load(open(
    "C:/Development/CHP model web/.winkers/units.json", encoding="utf-8",
))["units"]

data_units = [u for u in units if u.get("id", "").startswith("data:")]
print(f"=== {len(data_units)} data units ===")
for u in data_units:
    arts = u.get("hardcoded_artifacts", [])
    print(f"\n{u['id']}  ({len(arts)} artifacts)")
    desc = u.get("description", "")
    print(f"  desc: {desc[:200]}...")
    for a in arts[:5]:
        v = a.get("value")
        if isinstance(v, list):
            v = "[" + ", ".join(v[:3]) + ("…" if len(v) > 3 else "") + "]"
        surf = a.get("surface", "")
        kind = a.get("kind", "")
        ctx = a.get("context", "")[:60]
        print(f"    • {kind:11s} {str(v)[:25]:25s} {ctx}")

# Show couplings that newly include data files
print("\n=== Couplings touching data files ===")
data_files = {u["source_files"][0] for u in data_units if u.get("source_files")}
data_couplings = []
for u in units:
    if not u.get("id", "").startswith("coupling:"):
        continue
    consumer_files = {cn.get("file") for cn in u.get("consumers", [])}
    if consumer_files & data_files:
        data_couplings.append(u)

print(f"Found {len(data_couplings)} couplings linking data files to code")
for c in data_couplings[:5]:
    meta = c.get("meta", {})
    print(f"\n  value={meta.get('canonical_value')!r}  ({meta.get('primary_kind')})")
    for cn in c.get("consumers", [])[:4]:
        wtc = cn.get("what_to_check", "")[:80]
        print(f"    • {cn['file']:40s} :: {cn.get('anchor','')[:30]:30s}")
        print(f"      {wtc}")
