"""Spike: run GraphBuilder fresh on CHP project and report JS coverage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from collections import Counter
from winkers.graph import GraphBuilder

CHP = Path("C:/Development/CHP model web")

print("Building fresh graph on CHP...")
g = GraphBuilder().build(CHP)

langs = Counter(fn.language for fn in g.functions.values())
print()
print(f"Total functions: {len(g.functions)}")
print(f"Languages: {dict(langs)}")
print()

js_files = sorted({fn.file for fn in g.functions.values() if fn.language == "javascript"})
print(f"JS files indexed: {len(js_files)}")
for f in js_files:
    n = sum(1 for fn in g.functions.values() if fn.file == f)
    print(f"  {n:3d}  {f}")

print()
print("Sample JS function records:")
for fn in list(g.functions.values())[:0]:  # placeholder
    pass
js_fns = [fn for fn in g.functions.values() if fn.language == "javascript"]
for fn in js_fns[:5]:
    docs = (fn.docstring or "")[:60]
    print(f"  {fn.id:60s}  lines={fn.lines}  docstring={docs!r}")
