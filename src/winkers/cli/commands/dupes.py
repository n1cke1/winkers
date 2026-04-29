"""winkers dupes."""

from __future__ import annotations

import json
from pathlib import Path

import click

from winkers.store import GraphStore


@click.command("dupes")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--min-count", default=2, show_default=True,
              help="Minimum number of functions sharing a tag to report.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def dupes_cmd(path: str, min_count: int, as_json: bool):
    """Find duplicated inline logic via shared secondary_intents."""
    root = Path(path).resolve()
    graph = GraphStore(root).load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    groups: dict[str, list[str]] = {}
    for fn_id, fn in graph.functions.items():
        for tag in fn.secondary_intents or []:
            groups.setdefault(tag, []).append(fn_id)

    filtered = {
        tag: sorted(fns) for tag, fns in groups.items() if len(fns) >= min_count
    }
    filtered = dict(sorted(filtered.items(), key=lambda kv: -len(kv[1])))

    if as_json:
        click.echo(json.dumps({"groups": filtered}, indent=2))
        return

    if not filtered:
        click.echo(
            f"No duplicated logic found (threshold: {min_count}). "
            "Run `winkers init` with an API provider to populate secondary_intents."
        )
        return

    for tag, fn_ids in filtered.items():
        click.echo(f'"{tag}" ({len(fn_ids)} functions):')
        for fid in fn_ids:
            fn = graph.functions.get(fid)
            if fn is None:
                continue
            click.echo(f"  {fn.name}    {fn.file}:{fn.line_start}")
        click.echo()
