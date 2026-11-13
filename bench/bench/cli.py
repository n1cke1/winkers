import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from adapters import ADAPTERS, MODELS, get_adapter
from bench.matrix import (
    generate_matrix,
    generate_minimal_matrix,
    generate_full_matrix,
    format_matrix,
    estimate_cost,
)
from bench.runner import RunResult, load_config, run_single
from bench.report import generate_report
from bench.visualize import generate_all_visualizations

console = Console()


@click.group()
@click.option("--config", type=click.Path(exists=True), default=None, help="Config file path")
@click.pass_context
def main(ctx, config):
    ctx.ensure_object(dict)
    config_path = Path(config) if config else None
    ctx.obj["config"] = load_config(config_path)


@main.command()
@click.option("--project", "-p", required=True, help="Project name (httpx, invoicekit)")
@click.option("--task", "-t", default="all", help="Task ID or 'all'")
@click.option("--configs", "-c", default="H-BL", help="Comma-separated config labels (e.g. H-BL,S-WNK-SEM)")
@click.option("--source", "-s", type=click.Path(exists=True), help="Project source directory")
@click.option("--results-dir", "-r", default="results", help="Results output directory")
@click.option("--dry-run", is_flag=True, help="Show what would run without running")
@click.pass_context
def run(ctx, project, task, configs, source, results_dir, dry_run):
    config = ctx.obj["config"]
    results_path = Path(results_dir)

    if source is None:
        project_cfg = config.get("projects", {}).get(project, {})
        source = project_cfg.get("source", f"projects/{project}")
    source_path = Path(source)

    if not source_path.exists():
        console.print(f"[red]Project source not found: {source_path}[/red]")
        sys.exit(1)

    config_labels = [c.strip() for c in configs.split(",")]

    from bench.tasks.definitions import get_tasks_for_project, get_task
    if task == "all":
        tasks = get_tasks_for_project(project)
    elif task == "standard":
        from bench.tasks.definitions import get_standard_tasks
        tasks = get_standard_tasks(project)
    elif task == "trap":
        from bench.tasks.definitions import get_trap_tasks
        tasks = get_trap_tasks(project)
    else:
        tasks = [get_task(task)]

    entries = []
    for t in tasks:
        for label in config_labels:
            parts = label.split("-", 1)
            model_id = parts[0]
            adapter_id = parts[1] if len(parts) > 1 else "BL"
            entries.append((t.id, adapter_id, model_id))

    console.print(f"\n[bold]graph-mcp-bench[/bold]")
    console.print(f"Project: {project} ({source_path})")
    console.print(f"Runs: {len(entries)}")
    console.print()

    table = Table(title="Planned Runs")
    table.add_column("Task")
    table.add_column("Config")
    table.add_column("Model")
    table.add_column("Adapter")
    for task_id, adapter_id, model_id in entries:
        table.add_row(task_id, f"{model_id}-{adapter_id}", model_id, adapter_id)
    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run — no actual runs performed.[/yellow]")
        return

    results = []
    for i, (task_id, adapter_id, model_id) in enumerate(entries, 1):
        label = f"{model_id}-{adapter_id}"
        console.print(f"\n[bold cyan]Run {i}/{len(entries)}: {task_id} / {label}[/bold cyan]")

        try:
            adapter = get_adapter(adapter_id)
            adapter.install()
        except RuntimeError as e:
            console.print(f"[red]Adapter install failed: {e}[/red]")
            continue

        result = run_single(
            task_id=task_id,
            adapter_id=adapter_id,
            model_id=model_id,
            project_source=source_path,
            config=config,
            results_dir=results_path,
        )
        results.append(result)

        status = "[green]PASS[/green]" if result.tests_after.all_passed else "[red]FAIL[/red]"
        console.print(f"  Tests: {status} ({result.tests_after.passed} passed, {result.tests_after.failed} failed)")
        console.print(f"  Time: {result.wall_time_seconds:.0f}s, Cost: ${result.estimated_cost:.4f}, Turns: {result.num_turns}")
        if result.error:
            console.print(f"  [red]Error: {result.error}[/red]")

    if results:
        console.print("\n[bold]Generating report...[/bold]")
        report_path = generate_report(results, Path("reports"))
        console.print(f"Report: {report_path}")

        _save_results_index(results, results_path)


@main.command()
@click.option("--matrix", "-m", default="minimal", type=click.Choice(["minimal", "full", "custom"]))
@click.option("--projects", default=None, help="Comma-separated projects")
@click.option("--models", default=None, help="Comma-separated model IDs")
@click.option("--adapters", default=None, help="Comma-separated adapter IDs")
@click.pass_context
def plan(ctx, matrix, projects, models, adapters):
    if matrix == "minimal":
        entries = generate_minimal_matrix()
    elif matrix == "full":
        entries = generate_full_matrix()
    else:
        proj_list = projects.split(",") if projects else None
        model_list = models.split(",") if models else None
        adapter_list = adapters.split(",") if adapters else None
        entries = generate_matrix(
            projects=proj_list,
            models=model_list,
            adapters=adapter_list,
        )

    console.print(format_matrix(entries))
    est = estimate_cost(entries)
    console.print(f"\n[bold]Total runs:[/bold] {est['total_runs']}")
    console.print(f"[bold]Estimated cost:[/bold] ${est['total_estimated_cost']}")


@main.command()
@click.option("--results-dir", "-r", default="results", help="Results directory")
@click.option("--output-dir", "-o", default="reports", help="Reports output directory")
@click.pass_context
def report(ctx, results_dir, output_dir):
    results_path = Path(results_dir)
    if not results_path.exists():
        console.print("[red]No results found.[/red]")
        return

    results = _load_results(results_path)
    if not results:
        console.print("[yellow]No result data to report.[/yellow]")
        return

    report_path = generate_report(results, Path(output_dir))
    console.print(f"[green]Report generated: {report_path}[/green]")


@main.command()
@click.option("--task", "-t", required=True, help="Task ID")
@click.option("--compare", "-c", default=None, help="Comma-separated config labels to compare")
@click.option("--results-dir", "-r", default="results", help="Results directory")
@click.option("--output-dir", "-o", default="reports", help="Output directory")
@click.pass_context
def visualize(ctx, task, compare, results_dir, output_dir):
    results_path = Path(results_dir)
    results = _load_results(results_path)

    task_results = [r for r in results if r.task_id == task]
    if not task_results:
        console.print(f"[yellow]No results for task {task}[/yellow]")
        return

    compare_configs = [c.strip() for c in compare.split(",")] if compare else None

    generate_all_visualizations(task_results, Path(output_dir), compare_configs)
    console.print(f"[green]Visualizations generated in {output_dir}/[/green]")


@main.command(name="list")
def list_available():
    console.print("\n[bold]Adapters:[/bold]")
    table = Table()
    table.add_column("ID")
    table.add_column("Name")
    for sid, cls in sorted(ADAPTERS.items()):
        table.add_row(sid, cls.name)
    console.print(table)

    console.print("\n[bold]Models:[/bold]")
    table = Table()
    table.add_column("ID")
    table.add_column("Model")
    table.add_column("Input $/1M")
    table.add_column("Output $/1M")
    for mid, info in sorted(MODELS.items()):
        table.add_row(mid, info["id"], f"${info['input_price']:.2f}", f"${info['output_price']:.2f}")
    console.print(table)

    console.print("\n[bold]Tasks:[/bold]")
    from bench.tasks.definitions import ALL_TASKS
    table = Table()
    table.add_column("ID")
    table.add_column("Project")
    table.add_column("Category")
    table.add_column("Title")
    for tid, task in sorted(ALL_TASKS.items()):
        cat_color = "green" if task.category == "standard" else "red"
        table.add_row(tid, task.project, f"[{cat_color}]{task.category}[/{cat_color}]", task.title)
    console.print(table)


def _save_results_index(results: list[RunResult], results_dir: Path) -> None:
    index = []
    for r in results:
        index.append({
            "task_id": r.task_id,
            "config": r.config_label,
            "timestamp": r.timestamp,
            "tests_pass": r.tests_after.all_passed,
            "cost": r.estimated_cost,
            "wall_time": r.wall_time_seconds,
        })
    index_path = results_dir / "index.json"
    existing = []
    if index_path.exists():
        existing = json.loads(index_path.read_text())
    existing.extend(index)
    index_path.write_text(json.dumps(existing, indent=2))


def _load_results(results_dir: Path) -> list[RunResult]:
    results = []
    if not results_dir.exists():
        return results

    for run_dir in sorted(results_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        summary_file = run_dir / "summary.json"
        claude_file = run_dir / "claude_output.json"
        if not summary_file.exists():
            continue

        summary = json.loads(summary_file.read_text())
        claude_output = {}
        if claude_file.exists():
            claude_output = json.loads(claude_file.read_text())

        from bench.runner import TestResult
        result = RunResult(
            task_id=summary.get("task_id", ""),
            adapter_id=summary.get("config", "").split("-", 1)[-1] if "-" in summary.get("config", "") else "BL",
            model_id=summary.get("config", "").split("-", 1)[0] if "-" in summary.get("config", "") else "H",
            config_label=summary.get("config", ""),
            timestamp=run_dir.name.split("_")[0] if "_" in run_dir.name else "",
            tests_before=TestResult(
                passed=summary.get("tests_before_passed", 0),
                failed=0, errors=0,
                total=summary.get("tests_before_passed", 0),
                exit_code=0, output="",
            ),
            tests_after=TestResult(
                passed=summary.get("tests_after_passed", 0),
                failed=summary.get("tests_after_failed", 0),
                errors=0,
                total=summary.get("tests_after_passed", 0) + summary.get("tests_after_failed", 0),
                exit_code=0 if summary.get("tests_after_failed", 0) == 0 else 1,
                output="",
            ),
            claude_output=claude_output,
            wall_time_seconds=0,
        )
        results.append(result)

    return results


if __name__ == "__main__":
    main()
