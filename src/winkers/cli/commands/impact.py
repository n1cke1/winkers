"""winkers impact."""

from __future__ import annotations

import json
from pathlib import Path

import click

from winkers.store import GraphStore


@click.command("impact")
@click.argument("fn_query")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def impact_cmd(fn_query: str, path: str, as_json: bool):
    """Print pre-computed impact analysis for a function.

    \b
    Examples:
        winkers impact calculate_price
        winkers impact modules/pricing.py::calculate_price
        winkers impact build_graph --json
    """
    root = Path(path).resolve()
    graph = GraphStore(root).load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    from winkers.impact import ImpactStore

    impact = ImpactStore(root).load()
    if not impact.functions:
        click.echo(
            "No impact.json found. Run `winkers init` with an API provider "
            "(ANTHROPIC_API_KEY set) to generate it."
        )
        raise SystemExit(1)

    # Exact fn_id first, then by short name
    fn_id = fn_query if fn_query in graph.functions else None
    if fn_id is None:
        hits = [fid for fid in graph.functions if fid.endswith(f"::{fn_query}")]
        if len(hits) == 1:
            fn_id = hits[0]
        elif len(hits) > 1:
            click.echo(f"Ambiguous name '{fn_query}'. Candidates:")
            for h in hits:
                click.echo(f"  {h}")
            raise SystemExit(1)

    if fn_id is None:
        click.echo(f"Function not found: {fn_query}", err=True)
        raise SystemExit(1)

    report = impact.functions.get(fn_id)
    if report is None:
        click.echo(f"No impact report for {fn_id}. Re-run `winkers init`.")
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps({
            "function": fn_id,
            "report": report.model_dump(),
        }, indent=2))
        return

    click.echo(f"{fn_id}")
    click.echo(f"  risk:    {report.risk_level} ({report.risk_score:.2f})")
    click.echo(f"  summary: {report.summary}")
    if report.safe_operations:
        click.echo(f"  safe:        {', '.join(report.safe_operations)}")
    if report.dangerous_operations:
        click.echo(f"  dangerous:   {', '.join(report.dangerous_operations)}")
    if report.caller_classifications:
        click.echo("  callers:")
        for cc in report.caller_classifications:
            click.echo(
                f"    {cc.caller}  {cc.dependency_type}  "
                f"{cc.coupling}  {cc.update_effort}"
            )
            if cc.note:
                click.echo(f"      ↳ {cc.note}")
    if report.action_plan:
        click.echo(f"  action_plan: {report.action_plan}")
