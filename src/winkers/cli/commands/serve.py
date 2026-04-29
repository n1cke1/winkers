"""winkers serve."""

from __future__ import annotations

from pathlib import Path

import click


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def serve(path: str):
    """Start the MCP server (stdio). AI agents connect here."""
    from winkers.mcp.server import run
    root = Path(path).resolve()
    run(root)
