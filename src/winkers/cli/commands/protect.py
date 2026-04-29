"""winkers protect."""

from __future__ import annotations

from pathlib import Path

import click

from winkers.store import GraphStore


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--startup", is_flag=True, default=False,
              help="Detect entry point and trace import chain.")
@click.option("--entry", default=None,
              help="Override entry point file (e.g. app.py).")
def protect(path: str, startup: bool, entry: str | None):
    """Mark critical files that must not break.

    \b
    winkers protect --startup          Auto-detect entry point
    winkers protect --startup --entry app.py   Use specific entry
    """
    if not startup:
        click.echo("Use --startup to trace the startup import chain.")
        return

    root = Path(path).resolve()

    from winkers.protect import (
        detect_entry_point,
        save_protect_config,
        trace_startup_chain,
    )

    graph = GraphStore(root).load()
    if graph is None:
        click.echo("No graph.json. Run: winkers init")
        return

    if entry is None:
        entry = detect_entry_point(graph)
    if entry is None:
        click.echo(
            "No entry point found. Use --entry to specify "
            "(e.g. winkers protect --startup --entry app.py)"
        )
        return

    if entry not in graph.files:
        click.echo(f"Entry point '{entry}' not found in graph.")
        return

    chain = trace_startup_chain(graph, entry)
    save_protect_config(root, entry, chain)
    click.echo(
        f"  [ok] Startup chain: {entry} -> {len(chain)} files protected.\n"
        f"  Chain: {', '.join(chain)}"
    )
