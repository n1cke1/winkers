import json
from dataclasses import dataclass
from pathlib import Path

from bench.metrics import CollectedMetrics, collect_metrics
from bench.runner import RunResult


@dataclass
class ReportRow:
    config_label: str
    task_id: str
    tests_pass: bool
    tests_before: int
    tests_after: int
    new_tests: int
    files_modified: int
    files_created: int
    quality_score: float
    estimated_cost: float
    qpd: float
    wall_time: float
    input_tokens: int
    output_tokens: int
    graph_calls: int
    trap_score: int | None = None


def generate_report(results: list[RunResult], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for result in results:
        from bench.tasks.definitions import get_task
        task = get_task(result.task_id)
        is_trap = task.category == "trap"
        metrics = collect_metrics(result, is_trap=is_trap)

        row = ReportRow(
            config_label=result.config_label,
            task_id=result.task_id,
            tests_pass=metrics.quality.tests_pass,
            tests_before=metrics.quality.tests_before_count,
            tests_after=metrics.quality.tests_after_count,
            new_tests=metrics.quality.new_tests_written,
            files_modified=metrics.quality.files_modified,
            files_created=metrics.quality.files_created,
            quality_score=metrics.quality_score,
            estimated_cost=metrics.efficiency.estimated_cost,
            qpd=metrics.qpd,
            wall_time=metrics.efficiency.wall_time_seconds,
            input_tokens=metrics.efficiency.input_tokens,
            output_tokens=metrics.efficiency.output_tokens,
            graph_calls=metrics.efficiency.graph_tool_calls,
            trap_score=metrics.trap.trap_score if metrics.trap else None,
        )
        rows.append(row)

    md = _render_markdown(rows)
    md_path = output_dir / "summary.md"
    md_path.write_text(md)

    json_data = [r.__dict__ for r in rows]
    json_path = output_dir / "summary.json"
    json_path.write_text(json.dumps(json_data, indent=2))

    return md_path


def _render_markdown(rows: list[ReportRow]) -> str:
    lines = []
    lines.append("# graph-mcp-bench Results\n")

    standard_rows = [r for r in rows if r.trap_score is None]
    trap_rows = [r for r in rows if r.trap_score is not None]

    if standard_rows:
        lines.append("## Standard Tasks\n")
        lines.append(
            "| Config | Task | Tests | New Tests | Files Mod | Files New | "
            "Quality | Cost | QPD | Time |"
        )
        lines.append(
            "|--------|------|-------|-----------|-----------|-----------|"
            "---------|------|-----|------|"
        )
        for r in sorted(standard_rows, key=lambda x: (x.task_id, x.config_label)):
            tests = "PASS" if r.tests_pass else "FAIL"
            lines.append(
                f"| {r.config_label} | {r.task_id} | {tests} | {r.new_tests} | "
                f"{r.files_modified} | {r.files_created} | "
                f"{r.quality_score:.0f} | ${r.estimated_cost:.2f} | "
                f"{r.qpd:.0f} | {r.wall_time:.0f}s |"
            )

    if trap_rows:
        lines.append("\n## Adversarial (Trap) Tasks\n")
        lines.append(
            "| Config | Task | Tests | Trap Score | Safe Impl | Cost | Time |"
        )
        lines.append(
            "|--------|------|-------|------------|-----------|------|------|"
        )
        for r in sorted(trap_rows, key=lambda x: (x.task_id, x.config_label)):
            tests = "PASS" if r.tests_pass else "FAIL"
            lines.append(
                f"| {r.config_label} | {r.task_id} | {tests} | "
                f"{r.trap_score}/100 | "
                f"{'yes' if r.trap_score and r.trap_score >= 50 else 'no'} | "
                f"${r.estimated_cost:.2f} | {r.wall_time:.0f}s |"
            )

    if standard_rows:
        lines.append("\n## Summary by Configuration\n")
        by_config = {}
        for r in standard_rows:
            by_config.setdefault(r.config_label, []).append(r)

        lines.append("| Config | Avg Quality | Avg QPD | Avg Cost | Pass Rate |")
        lines.append("|--------|-------------|---------|----------|-----------|")
        for config, config_rows in sorted(by_config.items()):
            avg_q = sum(r.quality_score for r in config_rows) / len(config_rows)
            avg_qpd = sum(r.qpd for r in config_rows) / len(config_rows)
            avg_cost = sum(r.estimated_cost for r in config_rows) / len(config_rows)
            pass_rate = sum(1 for r in config_rows if r.tests_pass) / len(config_rows)
            lines.append(
                f"| {config} | {avg_q:.0f} | {avg_qpd:.0f} | "
                f"${avg_cost:.2f} | {pass_rate:.0%} |"
            )

    if trap_rows:
        lines.append("\n## Trap Task Summary\n")
        by_config = {}
        for r in trap_rows:
            by_config.setdefault(r.config_label, []).append(r)

        lines.append("| Config | Avg Trap Score | Safe Rate |")
        lines.append("|--------|----------------|-----------|")
        for config, config_rows in sorted(by_config.items()):
            avg_trap = sum(r.trap_score or 0 for r in config_rows) / len(config_rows)
            safe_rate = sum(1 for r in config_rows if r.trap_score and r.trap_score >= 50) / len(config_rows)
            lines.append(f"| {config} | {avg_trap:.0f}/100 | {safe_rate:.0%} |")

    return "\n".join(lines)
