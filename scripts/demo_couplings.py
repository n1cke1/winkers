"""Show diverse real coupling examples from CHP units.json.

Picks one cluster per kind (id_list, route, identifier, count w/ surface,
threshold w/ surface) plus one cross-language and one backend-frontend
example for illustration.
"""
import json

UNITS = json.load(open("C:/Development/CHP model web/.winkers/units.json",
                       encoding="utf-8"))["units"]
COUPLINGS = [u for u in UNITS if u.get("id", "").startswith("coupling:")]


def show(c: dict, label: str = "") -> None:
    meta = c.get("meta", {})
    if label:
        print(f"\n┌─ {label}")
    print(f"│ value: {meta.get('canonical_value')!r}")
    print(f"│ kind={meta.get('primary_kind')}, "
          f"files={meta.get('file_count')}, hits={meta.get('hit_count')}")
    print("│")
    for cn in c.get("consumers", []):
        anchor = cn.get("anchor", "")
        if "::" in anchor:
            short = anchor.split("::", 1)[-1]
        else:
            short = anchor
        print(f"│ • {cn['file']:35s}  {short[:30]}")
        wtc = cn.get("what_to_check", "")
        if wtc:
            print(f"│   {wtc[:100]}")
    print("└─")


def find_one_by(predicate, label):
    for c in COUPLINGS:
        if predicate(c):
            show(c, label)
            return c


def is_cross_language(c):
    files = {cn["file"] for cn in c.get("consumers", [])}
    has_py = any(f.endswith(".py") for f in files)
    has_js = any(f.endswith(".js") for f in files)
    return has_py and has_js


def is_route(c):
    return c.get("meta", {}).get("primary_kind") == "route"


def is_threshold_with_surface(c):
    if c.get("meta", {}).get("primary_kind") != "threshold":
        return False
    return any(cn.get("surface", "") and cn.get("surface", "") not in ("", cn.get("anchor", ""))
               for cn in c.get("consumers", []))


def is_count_with_surface(c):
    if c.get("meta", {}).get("primary_kind") != "count":
        return False
    return any(cn.get("surface", "") for cn in c.get("consumers", []))


def is_id_list(c):
    return c.get("meta", {}).get("primary_kind") == "id_list"


def is_named_identifier(c):
    if c.get("meta", {}).get("primary_kind") != "identifier":
        return False
    val = c.get("meta", {}).get("canonical_value", "")
    return len(val) >= 4 and ("_" in val or val[0].isalpha())


def is_html_dom(c):
    """Coupling between HTML id and JS that reads it."""
    files = {cn["file"] for cn in c.get("consumers", [])}
    has_html = any(f.endswith(".html") for f in files)
    has_js = any(f.endswith(".js") for f in files)
    return has_html and has_js


SHOWN_IDS = set()

def show_unique(predicate, label):
    for c in COUPLINGS:
        if c["id"] in SHOWN_IDS:
            continue
        if predicate(c):
            show(c, label)
            SHOWN_IDS.add(c["id"])
            return c


print("=" * 75)
print(f"CHP project: {len(COUPLINGS)} coupling clusters total (after filter)")
print("=" * 75)

show_unique(is_route, "1. ROUTE — backend Flask + frontend JS fetch")

show_unique(is_id_list, "2. ID_LIST — turbine names cross-language")

show_unique(is_count_with_surface, "3. COUNT WITH SURFACE — load-bearing counter")

show_unique(is_threshold_with_surface,
            "4. THRESHOLD WITH SURFACE — domain constant")

show_unique(is_html_dom, "5. HTML DOM ↔ JS — id contract")

show_unique(is_named_identifier, "6. NAMED IDENTIFIER — config key cross-file")

# Pick a 7th showing depth — large multi-file cluster
big = sorted(COUPLINGS, key=lambda c: c.get("meta", {}).get("file_count", 0),
             reverse=True)[0]
if big["id"] not in SHOWN_IDS:
    show(big, "7. WIDEST — cluster spanning the most files")
