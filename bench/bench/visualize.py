import json
from pathlib import Path

from bench.metrics import CollectedMetrics, collect_metrics
from bench.runner import RunResult


def generate_heatmap_data(results: list[RunResult]) -> dict:
    by_task = {}
    for result in results:
        by_task.setdefault(result.task_id, {})[result.config_label] = {
            "files_modified": [],
            "files_created": [],
            "lines_added": 0,
            "lines_removed": 0,
        }

        for call in result.tool_calls:
            name = call.get("name", "")
            path = call.get("input", {}).get("file_path", "")
            if name == "Edit" and path:
                by_task[result.task_id][result.config_label]["files_modified"].append(path)
            elif name == "Write" and path:
                by_task[result.task_id][result.config_label]["files_created"].append(path)

    return by_task


def generate_function_diff(results: list[RunResult]) -> dict:
    by_task = {}
    for result in results:
        edits = []
        for call in result.tool_calls:
            if call.get("name") == "Edit":
                inp = call.get("input", {})
                edits.append({
                    "file": inp.get("file_path", ""),
                    "old_string_preview": (inp.get("old_string", ""))[:80],
                    "new_string_preview": (inp.get("new_string", ""))[:80],
                })
            elif call.get("name") == "Write":
                inp = call.get("input", {})
                content = inp.get("content", "")
                edits.append({
                    "file": inp.get("file_path", ""),
                    "type": "new_file",
                    "lines": content.count("\n") + 1,
                })
        by_task.setdefault(result.task_id, {})[result.config_label] = edits

    return by_task


def generate_dependency_mermaid(results: list[RunResult], task_id: str) -> str:
    task_results = [r for r in results if r.task_id == task_id]
    if not task_results:
        return ""

    lines = ["graph LR"]
    for result in task_results:
        config = result.config_label
        lines.append(f'    subgraph "{config}"')

        files_touched = set()
        for call in result.tool_calls:
            name = call.get("name", "")
            path = call.get("input", {}).get("file_path", "")
            if path and name in ("Edit", "Write"):
                short = Path(path).name
                files_touched.add(short)

        for f in sorted(files_touched):
            lines.append(f"    {config}_{f.replace('.', '_')}[{f}]")

        lines.append("    end")

    return "\n".join(lines)


def generate_side_by_side(results: list[RunResult], task_id: str) -> str:
    task_results = [r for r in results if r.task_id == task_id]
    if len(task_results) < 2:
        return "Need at least 2 configurations to compare."

    lines = [f"# Side-by-side: {task_id}\n"]

    for result in sorted(task_results, key=lambda r: r.config_label):
        from bench.tasks.definitions import get_task
        task = get_task(result.task_id)
        is_trap = task.category == "trap"
        metrics = collect_metrics(result, is_trap=is_trap)

        lines.append(f"## {result.config_label}\n")
        lines.append(f"- Quality score: {metrics.quality_score:.0f}")
        lines.append(f"- Tests: {'PASS' if metrics.quality.tests_pass else 'FAIL'}")
        lines.append(f"- Files modified: {metrics.quality.files_modified}")
        lines.append(f"- Files created: {metrics.quality.files_created}")
        lines.append(f"- Cost: ${metrics.efficiency.estimated_cost:.2f}")
        lines.append(f"- Wall time: {metrics.efficiency.wall_time_seconds:.0f}s")
        lines.append(f"- Graph tool calls: {metrics.efficiency.graph_tool_calls}")
        if metrics.trap:
            lines.append(f"- Trap score: {metrics.trap.trap_score}/100")
        lines.append("")

    return "\n".join(lines)


def generate_all_visualizations(
    results: list[RunResult],
    output_dir: Path,
    compare_configs: list[str] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    if compare_configs:
        results = [r for r in results if r.config_label in compare_configs]

    heatmap = generate_heatmap_data(results)
    (output_dir / "heatmap_data.json").write_text(json.dumps(heatmap, indent=2))

    func_diff = generate_function_diff(results)
    (output_dir / "function_diff.json").write_text(json.dumps(func_diff, indent=2))

    task_ids = sorted(set(r.task_id for r in results))
    for task_id in task_ids:
        mermaid = generate_dependency_mermaid(results, task_id)
        if mermaid:
            (output_dir / f"{task_id}_deps.mermaid").write_text(mermaid)

        side_by_side = generate_side_by_side(results, task_id)
        (output_dir / f"{task_id}_side_by_side.md").write_text(side_by_side)
