"""winkers improve."""

from __future__ import annotations

from pathlib import Path

import click

from winkers.cli.init_pipeline import (
    _backup_file,
)


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--apply", "do_apply", is_flag=True, default=False,
              help="Inject high-priority insights into semantic.json.")
def improve(path: str, do_apply: bool):
    """Show or apply accumulated insights from session analysis.

    \b
    Default (dry-run): show open insights grouped by priority.
    --apply: inject high-priority injection_content into
             semantic.json constraints[], backup, mark as fixed.
    """
    root = Path(path).resolve()

    from winkers.insights_store import InsightsStore
    insights_store = InsightsStore(root)
    open_items = insights_store.open_insights()

    if not open_items:
        click.echo("No open insights. Run: winkers analyze")
        return

    # Display insights
    for i, item in enumerate(open_items):
        marker = "*" if item.priority == "high" else "-"
        occ = f" (x{item.occurrences})" if item.occurrences > 1 else ""
        click.echo(
            f"  {marker} [{item.priority}] {item.category}{occ}: "
            f"{item.description}"
        )
        click.echo(f"    -> {item.injection_content}")

    high = [it for it in open_items if it.priority == "high"]
    click.echo(
        f"\n{len(open_items)} open insight(s), {len(high)} high-priority."
    )

    if not do_apply:
        click.echo("Run with --apply to inject high-priority insights.")
        return

    if not high:
        click.echo("No high-priority insights to apply.")
        return

    from winkers.semantic import SemanticStore
    sem_store = SemanticStore(root)
    sem = sem_store.load()
    if sem is None:
        click.echo("No semantic.json. Run: winkers init")
        return

    # Backup before modifying
    _backup_file(sem_store.semantic_path, root / ".winkers" / "history", "semantic")

    applied_indices: list[int] = []
    for i, item in enumerate(open_items):
        if item.priority != "high":
            continue
        if item.injection_content and item.injection_content not in sem.constraints:
            sem.constraints.append(item.injection_content)
        applied_indices.append(i)

    sem_store.save(sem)
    insights_store.mark_fixed(applied_indices)

    click.echo(
        f"Applied {len(applied_indices)} insight(s) to semantic.json constraints."
    )
