"""Aggregate all run folders under results/ into a comprehensive report
for successfully completed tasks.

Usage: python -m bench.aggregate_report
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

GRAPH_PREFIXES = ("mcp__wnk__", "mcp__cgc__", "mcp__cbm__")
NAV_TOOLS = {"Read", "Grep", "Glob", "Bash"}
MOD_TOOLS = {"Write", "Edit"}

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def load_runs():
    runs = []
    for folder in sorted(RESULTS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        summary_path = folder / "summary.json"
        if not summary_path.exists():
            continue
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data["_folder"] = folder.name
        data["_timestamp"] = folder.name.split("_", 2)[:2]
        runs.append(data)
    return runs


def classify_tools(tool_stats: dict) -> dict:
    graph = nav = mod = other = 0
    for name, count in (tool_stats or {}).items():
        if any(name.startswith(p) for p in GRAPH_PREFIXES):
            graph += count
        elif name in NAV_TOOLS:
            nav += count
        elif name in MOD_TOOLS:
            mod += count
        else:
            other += count
    return {"graph": graph, "nav": nav, "mod": mod, "other": other}


def is_success(run: dict) -> bool:
    # Completed = tests_after passed >= before AND no test failures AND non-trivial wall time
    after_failed = run.get("tests_after_failed", 0)
    after_passed = run.get("tests_after_passed", 0)
    duration_ms = run.get("duration_ms", 0) or 0
    if duration_ms < 10_000:
        return False
    if after_failed > 0:
        return False
    if after_passed < run.get("tests_before_passed", 0):
        return False
    return True


def fmt_cost(v: float) -> str:
    return f"${v:.2f}"


def render_report(runs: list[dict]) -> str:
    successful = [r for r in runs if is_success(r)]

    lines = []
    lines.append("# graph-mcp-bench — полный отчёт по успешно завершённым задачам\n")
    lines.append(
        f"Всего запусков в results/: **{len(runs)}**, "
        f"успешно завершены: **{len(successful)}**\n"
    )
    lines.append(
        "Критерий успеха: tests_after_failed=0, tests_after_passed ≥ tests_before_passed, "
        "duration ≥ 10с (исключает сломанные прогоны с нулевым временем).\n"
    )

    # ---- 1. Детальная таблица по каждому запуску ----
    lines.append("## Детально по каждому запуску\n")
    lines.append(
        "| Time | Config | Task | Task Title | Tests before→after | New | Cost | Wall s | Turns | Tools | Graph | Nav | Mod |"
    )
    lines.append(
        "|------|--------|------|------------|--------------------|-----|------|--------|-------|-------|-------|-----|-----|"
    )
    for r in sorted(successful, key=lambda x: x["_folder"]):
        tools = classify_tools(r.get("tool_stats", {}))
        before = r.get("tests_before_passed", 0)
        after = r.get("tests_after_passed", 0)
        new = max(0, after - before)
        wall = (r.get("duration_ms", 0) or 0) / 1000
        title = r.get("task_title", "")[:30]
        ts = r["_folder"].split("_", 2)
        when = f"{ts[0]} {ts[1][:4]}"
        lines.append(
            f"| {when} | {r.get('config','')} | {r.get('task_id','')} | {title} "
            f"| {before}→{after} | {new} | {fmt_cost(r.get('cost_usd',0))} "
            f"| {wall:.0f} | {r.get('num_turns',0)} "
            f"| {r.get('total_tool_calls',0)} | {tools['graph']} | {tools['nav']} | {tools['mod']} |"
        )

    # ---- 2. По задаче × конфигу (best run wins) ----
    lines.append("\n## Свод по задачам и конфигам (лучший run по cost при PASS)\n")

    by_tc = defaultdict(list)
    for r in successful:
        by_tc[(r.get("task_id"), r.get("config"))].append(r)

    task_ids = sorted({k[0] for k in by_tc.keys()})
    configs = sorted({k[1] for k in by_tc.keys()})

    lines.append("### Цена лучшего прохода (minimum cost) $\n")
    lines.append("| Task \\ Config | " + " | ".join(configs) + " |")
    lines.append("|" + "---|" * (len(configs) + 1))
    for tid in task_ids:
        row = [tid]
        for cfg in configs:
            runs_tc = by_tc.get((tid, cfg), [])
            if runs_tc:
                best = min(runs_tc, key=lambda x: x.get("cost_usd", 0) or 0)
                row.append(f"{fmt_cost(best.get('cost_usd',0))}")
            else:
                row.append("—")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n### Среднее время лучшего прохода, с\n")
    lines.append("| Task \\ Config | " + " | ".join(configs) + " |")
    lines.append("|" + "---|" * (len(configs) + 1))
    for tid in task_ids:
        row = [tid]
        for cfg in configs:
            runs_tc = by_tc.get((tid, cfg), [])
            if runs_tc:
                best = min(runs_tc, key=lambda x: x.get("cost_usd", 0) or 0)
                wall = (best.get("duration_ms", 0) or 0) / 1000
                row.append(f"{wall:.0f}")
            else:
                row.append("—")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n### Кол-во успешных запусков\n")
    lines.append("| Task \\ Config | " + " | ".join(configs) + " | Total |")
    lines.append("|" + "---|" * (len(configs) + 2))
    col_totals = defaultdict(int)
    grand = 0
    for tid in task_ids:
        row = [tid]
        tot = 0
        for cfg in configs:
            n = len(by_tc.get((tid, cfg), []))
            row.append(str(n) if n else "—")
            col_totals[cfg] += n
            tot += n
        row.append(str(tot))
        grand += tot
        lines.append("| " + " | ".join(row) + " |")
    totals_row = ["**Total**"] + [str(col_totals[c]) for c in configs] + [f"**{grand}**"]
    lines.append("| " + " | ".join(totals_row) + " |")

    # ---- 3. Агрегат по конфигу ----
    lines.append("\n## Агрегат по конфигурации (только успешные прогоны)\n")
    by_cfg = defaultdict(list)
    for r in successful:
        by_cfg[r.get("config")].append(r)

    lines.append(
        "| Config | Runs | Avg cost $ | Median cost $ | Avg wall s | Avg turns | Avg graph calls | Avg new tests |"
    )
    lines.append("|" + "---|" * 8)
    for cfg in sorted(by_cfg):
        rows = by_cfg[cfg]
        costs = [r.get("cost_usd", 0) or 0 for r in rows]
        walls = [(r.get("duration_ms", 0) or 0) / 1000 for r in rows]
        turns = [r.get("num_turns", 0) or 0 for r in rows]
        gcalls = [classify_tools(r.get("tool_stats", {}))["graph"] for r in rows]
        new_tests = [
            max(0, (r.get("tests_after_passed", 0) - r.get("tests_before_passed", 0)))
            for r in rows
        ]
        lines.append(
            f"| {cfg} | {len(rows)} | "
            f"{mean(costs):.2f} | {median(costs):.2f} | "
            f"{mean(walls):.0f} | {mean(turns):.1f} | "
            f"{mean(gcalls):.1f} | {mean(new_tests):.1f} |"
        )

    # ---- 4. Топ эффективных ----
    lines.append("\n## Топ-10 самых дешёвых успешных прогонов\n")
    lines.append("| # | Config | Task | Cost $ | Wall s | Turns | Graph calls | New tests |")
    lines.append("|---|--------|------|--------|--------|-------|-------------|-----------|")
    top = sorted(successful, key=lambda r: r.get("cost_usd", 0) or 0)[:10]
    for i, r in enumerate(top, 1):
        tools = classify_tools(r.get("tool_stats", {}))
        before = r.get("tests_before_passed", 0)
        after = r.get("tests_after_passed", 0)
        new = max(0, after - before)
        wall = (r.get("duration_ms", 0) or 0) / 1000
        lines.append(
            f"| {i} | {r.get('config')} | {r.get('task_id')} | "
            f"{fmt_cost(r.get('cost_usd',0))} | {wall:.0f} | {r.get('num_turns',0)} | "
            f"{tools['graph']} | {new} |"
        )

    lines.append("\n## Топ-10 самых быстрых успешных прогонов\n")
    lines.append("| # | Config | Task | Wall s | Cost $ | Turns | Graph calls | New tests |")
    lines.append("|---|--------|------|--------|--------|-------|-------------|-----------|")
    top = sorted(successful, key=lambda r: r.get("duration_ms", 0) or 0)[:10]
    for i, r in enumerate(top, 1):
        tools = classify_tools(r.get("tool_stats", {}))
        before = r.get("tests_before_passed", 0)
        after = r.get("tests_after_passed", 0)
        new = max(0, after - before)
        wall = (r.get("duration_ms", 0) or 0) / 1000
        lines.append(
            f"| {i} | {r.get('config')} | {r.get('task_id')} | "
            f"{wall:.0f} | {fmt_cost(r.get('cost_usd',0))} | {r.get('num_turns',0)} | "
            f"{tools['graph']} | {new} |"
        )

    return "\n".join(lines)


def main():
    runs = load_runs()
    report = render_report(runs)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "full_report.md"
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out} ({len(report)} chars)")


if __name__ == "__main__":
    main()
