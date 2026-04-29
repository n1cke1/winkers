"""winkers dashboard."""

from __future__ import annotations

from pathlib import Path

import click

from winkers.cli.init_pipeline import (
    _collect_git_history,
    _save_history_snapshot,
)
from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--port", default=7420, show_default=True, help="HTTP port")
@click.option("--no-browser", is_flag=True, default=False, help="Don't open browser")
def dashboard(path: str, port: int, no_browser: bool):
    """Open the browser dependency graph.

    Rebuilds the graph before opening to ensure fresh data.
    """
    import webbrowser

    from winkers.dashboard.api import run as run_dashboard

    root = Path(path).resolve()

    # Rebuild graph for fresh data
    click.echo("Rebuilding graph ...")
    builder = GraphBuilder()
    graph = builder.build(root)
    CrossFileResolver().resolve(graph, str(root))
    _collect_git_history(root, graph)
    store = GraphStore(root)
    store.save(graph)
    _save_history_snapshot(root, graph)
    click.echo(
        f"  {len(graph.files)} files, {len(graph.functions)} functions, "
        f"{len(graph.call_edges)} edges"
    )

    url = f"http://127.0.0.1:{port}"
    click.echo(f"Dashboard at {url}")
    if not no_browser:
        webbrowser.open(url)
    run_dashboard(root, port=port)
