from dataclasses import dataclass
from itertools import product
from pathlib import Path

from adapters import ADAPTERS, MODELS
from bench.tasks.definitions import get_tasks_for_project, get_standard_tasks, get_trap_tasks


@dataclass
class MatrixEntry:
    task_id: str
    adapter_id: str
    model_id: str
    project: str

    @property
    def config_label(self) -> str:
        return f"{self.model_id}-{self.adapter_id}"

    @property
    def run_label(self) -> str:
        return f"{self.config_label}_{self.task_id}"


def generate_matrix(
    projects: list[str] | None = None,
    models: list[str] | None = None,
    adapters: list[str] | None = None,
    task_ids: list[str] | None = None,
    task_categories: list[str] | None = None,
) -> list[MatrixEntry]:
    if models is None:
        models = list(MODELS.keys())
    if adapters is None:
        adapters = list(ADAPTERS.keys())
    if projects is None:
        projects = ["invoicekit"]

    entries = []
    for proj in projects:
        tasks = get_tasks_for_project(proj)

        if task_ids:
            tasks = [t for t in tasks if t.id in task_ids]
        if task_categories:
            tasks = [t for t in tasks if t.category in task_categories]

        for task, model_id, adapter_id in product(tasks, models, adapters):
            entries.append(MatrixEntry(
                task_id=task.id,
                adapter_id=adapter_id,
                model_id=model_id,
                project=proj,
            ))

    return entries


def generate_minimal_matrix() -> list[MatrixEntry]:
    return generate_matrix(
        projects=["invoicekit"],
        models=["H", "S"],
        adapters=["BL", "CBM", "WNK"],
        task_categories=["standard"],
    )


def generate_full_matrix() -> list[MatrixEntry]:
    # CGC excluded: 0.4.2 FalkorDB Lite backend has Windows cp1251 issues
    # and its MCP tools are not used reliably by the agent in past runs.
    # WNK-SEM merged into WNK (semantic.json is always present in source).
    return generate_matrix(
        projects=["httpx", "invoicekit"],
        models=["H", "S"],
        adapters=["BL", "CBM", "WNK"],
        task_categories=["standard", "trap"],
    )


def estimate_cost(entries: list[MatrixEntry]) -> dict:
    per_run_tokens = {
        "H": {"input": 50_000, "output": 15_000},
        "S": {"input": 50_000, "output": 15_000},
    }

    total_cost = 0.0
    by_model = {}

    for entry in entries:
        model = MODELS[entry.model_id]
        tokens = per_run_tokens[entry.model_id]
        cost = (
            tokens["input"] * model["input_price"] / 1_000_000 +
            tokens["output"] * model["output_price"] / 1_000_000
        )
        total_cost += cost
        by_model[entry.model_id] = by_model.get(entry.model_id, 0) + cost

    return {
        "total_runs": len(entries),
        "total_estimated_cost": round(total_cost, 2),
        "by_model": {k: round(v, 2) for k, v in by_model.items()},
        "estimated_time_hours": round(len(entries) * 5 / 60, 1),
    }


def format_matrix(entries: list[MatrixEntry]) -> str:
    lines = []
    lines.append(f"Matrix: {len(entries)} runs")
    lines.append("")

    by_project = {}
    for e in entries:
        by_project.setdefault(e.project, []).append(e)

    for proj, proj_entries in sorted(by_project.items()):
        lines.append(f"  {proj}:")
        by_task = {}
        for e in proj_entries:
            by_task.setdefault(e.task_id, []).append(e.config_label)
        for task_id, configs in sorted(by_task.items()):
            lines.append(f"    {task_id}: {', '.join(sorted(configs))}")

    est = estimate_cost(entries)
    lines.append("")
    lines.append(f"Estimated cost: ${est['total_estimated_cost']}")
    lines.append(f"Estimated time: ~{est['estimated_time_hours']}h")

    return "\n".join(lines)
