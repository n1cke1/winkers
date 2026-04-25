"""Demo: what an audit agent would see for one changed function.

Picks `engine/equations.py::build_constraints` from CHP units.json
and shows the function_unit + all coupling_units that reference it
+ their consumers (file + anchor + what_to_check).

This is exactly the material `winkers hook stop-audit` will pass to
the read-only audit subprocess in Phase 3.
"""
import json
import sys

UNITS = json.load(open("C:/Development/CHP model web/.winkers/units.json",
                       encoding="utf-8"))["units"]
BY_ID = {u["id"]: u for u in UNITS}


def show(fn_id: str) -> None:
    fn = BY_ID.get(fn_id)
    if fn is None:
        print(f"NOT FOUND: {fn_id}")
        return

    print("=" * 70)
    print(f"CHANGED FUNCTION: {fn['id']}")
    print("=" * 70)
    print()
    print("Description (truncated):")
    print(f"  {fn['description'][:400]}...")
    print()
    arts = fn.get("hardcoded_artifacts", []) or []
    print(f"Self-reported hardcoded_artifacts ({len(arts)}):")
    for a in arts:
        v = a["value"]
        if isinstance(v, list):
            v = "[" + ", ".join(v) + "]"
        surface = f' "{a["surface"]}"' if a.get("surface") else ""
        print(f"  • {a['kind']:11s} value={str(v)[:30]:30s}{surface}")
        print(f"    context: {a['context'][:90]}")
    print()

    # Find coupling units that reference this function as a member
    related = []
    for c in UNITS:
        if not c.get("id", "").startswith("coupling:"):
            continue
        if fn_id in c.get("source_anchors", []):
            related.append(c)

    print("=" * 70)
    print(f"COUPLED UNITS THAT REFERENCE THIS FUNCTION: {len(related)}")
    print("=" * 70)
    for c in related:
        meta = c.get("meta", {})
        print()
        print(f"  {c['id']}")
        print(f"    canonical_value: {meta.get('canonical_value')!r}")
        print(f"    kind={meta.get('primary_kind')}, "
              f"files={meta.get('file_count')}, hits={meta.get('hit_count')}")
        print(f"    consumers ({len(c.get('consumers', []))}):")
        for cn in c.get("consumers", []):
            anchor = cn.get("anchor", "")
            print(f"      • {cn['file']}  ::  {anchor[:50]}")
            wtc = cn.get("what_to_check", "")
            if wtc:
                print(f"        what_to_check: {wtc[:120]}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "engine/equations.py::build_constraints"
    show(target)
