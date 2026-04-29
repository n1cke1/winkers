"""winkers search."""

from __future__ import annotations

import json
from pathlib import Path

import click

from winkers.store import GraphStore


@click.command()
@click.argument("intent")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--zone", default="", help="Filter by zone name")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def search(intent: str, path: str, zone: str, as_json: bool):
    """Search project functions by intent.

    \b
    Examples:
        winkers search "calculate price"
        winkers search "validate email" --zone api
        winkers search "parse config" --json
    """
    root = Path(path).resolve()
    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    from winkers.search import (
        _fn_signature,
        format_before_create_response,
        get_pipeline_context,
        search_functions,
    )

    matches = search_functions(graph, intent, zone=zone)

    if as_json:
        result = format_before_create_response(graph, intent, matches, zone=zone, root=root)
        click.echo(json.dumps(result, indent=2))
        return

    if not matches:
        click.echo(f"No functions found matching: {intent}")
        return

    click.echo(f"Found {len(matches)} match(es) for \"{intent}\":\n")
    for i, m in enumerate(matches, 1):
        pipeline = get_pipeline_context(graph, m.fn.id)
        click.echo(f"  {i}. {m.fn.name}() in {m.fn.file}:{m.fn.line_start}")
        click.echo(f"     Signature: {_fn_signature(m.fn)}")
        click.echo(f"     Callers: {m.callers}  Score: {m.score}")
        if m.fn.docstring:
            click.echo(f"     Doc: {m.fn.docstring}")
        if pipeline.upstream:
            for fn in pipeline.upstream:
                click.echo(f"     ↑ {fn.name}{_fn_signature(fn)}  [{fn.file}:{fn.line_start}]")
        if pipeline.downstream:
            for fn in pipeline.downstream:
                click.echo(f"     ↓ {fn.name}{_fn_signature(fn)}  [{fn.file}:{fn.line_start}]")
        click.echo()
