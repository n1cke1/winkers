"""Smoke test: invoke find_work_area on Phase 0 spike artifacts.

Uses 39 real descriptions + BGE-M3 embeddings produced during the
spike on the CHP project. Confirms the MCP tool wiring works end-
to-end with the actual model loaded (no stub).
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from winkers.mcp.tools import _tool_find_work_area  # noqa: E402
from winkers.store import GraphStore  # noqa: E402


SPIKE_UNITS = Path(__file__).parent / "units.json"
SPIKE_EMBEDS = Path(__file__).parent / "embeddings.npz"
CHP_ROOT = Path("C:/Development/CHP model web")


def main() -> int:
    if not SPIKE_UNITS.exists() or not SPIKE_EMBEDS.exists():
        print("Spike artifacts missing — run scripts/build_units.py + "
              "scripts/embed_units.py first.")
        return 1

    # Sandbox: copy CHP graph + spike units into a temp .winkers/.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        winkers_dir = root / ".winkers"
        winkers_dir.mkdir()

        chp_graph = CHP_ROOT / ".winkers/graph.json"
        if not chp_graph.exists():
            print(f"Need CHP graph at {chp_graph}; run `winkers init` there.")
            return 1
        shutil.copy(chp_graph, winkers_dir / "graph.json")
        shutil.copy(SPIKE_UNITS, winkers_dir / "units.json")
        shutil.copy(SPIKE_EMBEDS, winkers_dir / "embeddings.npz")

        graph = GraphStore(root).load()
        print(f"Loaded graph: {len(graph.functions)} fns")

        queries = [
            "как устроен SLP-цикл сходимости",
            "где обновить счётчик переменных в Подходе",
            "куда добавить новую целевую функцию",
            "AI chat panel that creates tickets",
            "where to add 2FA auth login",  # should be NO_CLEAR_MATCH
        ]
        for q in queries:
            print(f"\n=== {q!r} ===")
            out = _tool_find_work_area(graph, {"query": q, "k": 3}, root)
            if "error" in out:
                print(f"  ERROR: {out['error']}")
                continue
            print(f"  verdict={out['verdict']}  conf={out['confidence']}  "
                  f"max={out['max_score']:.3f}")
            for m in out["matches"]:
                line = f"    {m['score']:.3f}  {m['kind']:18s} {m['id']:50s} {m.get('name', '')[:40]}"
                print(line)
                if "line_start" in m:
                    print(f"        → {m['file']}:{m['line_start']}-{m['line_end']}")
                if "source_anchors" in m:
                    for a in m["source_anchors"][:2]:
                        if "file" in a:
                            print(f"        → {a['file']}:{a['line_start']}  ({a['id']})")
                        else:
                            print(f"        → {a['id']} (unresolved)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
