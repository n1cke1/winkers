"""Rebuild the RESULTS.md tables from results/*/summary.json.

A run passes when no test fails after the change, no previously passing
test is lost, and the run lasted at least 10 s (zero-length runs are
harness crashes, not agent outcomes).

    python bench/summarize.py
"""
import collections
import json
import statistics as st
from pathlib import Path

RESULTS = Path(__file__).parent / "results"


def load():
    rows = []
    for f in sorted(RESULTS.glob("*/summary.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["run"] = f.parent.name
        rows.append(d)
    return rows


def passed(r):
    return (
        (r.get("tests_after_failed") or 0) == 0
        and (r.get("tests_after_passed") or 0) >= (r.get("tests_before_passed") or 0)
        and (r.get("duration_ms") or 0) >= 10_000
    )


def cost(rs):
    return st.mean(r.get("cost_usd") or 0 for r in rs)


def by_config(rows):
    print("| Config | Runs | Pass rate | Mean cost $ | Mean turns | Mean graph calls |")
    print("|---|---|---|---|---|---|")
    for c in sorted({r["config"] for r in rows}):
        rs = [r for r in rows if r["config"] == c]
        graph = [
            sum(v for k, v in (r.get("tool_stats") or {}).items() if k.startswith("mcp__"))
            for r in rs
        ]
        print(
            f"| {c} | {len(rs)} | {sum(map(passed, rs)) / len(rs):.0%} | {cost(rs):.2f} "
            f"| {st.mean(r.get('num_turns') or 0 for r in rs):.0f} | {st.mean(graph):.1f} |"
        )


def paired(rows, a="S-BL", b="S-WNK"):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["task_id"], r["config"])].append(r)
    tasks = sorted({r["task_id"] for r in rows}, key=lambda t: int(t[1:]))
    print(f"| Task | Kind | {a} runs | {a} pass | {a} cost $ | {b} runs | {b} pass | {b} cost $ | Cost delta |")
    print("|---|---|---|---|---|---|---|---|---|")
    for t in tasks:
        x, y = groups.get((t, a)), groups.get((t, b))
        if not (x and y):
            continue
        kind = x[0].get("task_category", "")
        print(
            f"| {t} | {kind} | {len(x)} | {sum(map(passed, x)) / len(x):.0%} | {cost(x):.2f} "
            f"| {len(y)} | {sum(map(passed, y)) / len(y):.0%} | {cost(y):.2f} "
            f"| {cost(y) / cost(x) - 1:+.0%} |"
        )


if __name__ == "__main__":
    rows = load()
    first, last = min(r["run"][:8] for r in rows), max(r["run"][:8] for r in rows)
    print(f"{len(rows)} runs, {first}..{last}\n")
    by_config(rows)
    print()
    paired(rows)
