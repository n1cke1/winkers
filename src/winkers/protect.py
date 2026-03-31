"""Startup chain protection — trace import chains from entry points."""

from __future__ import annotations

import json
from pathlib import Path

from winkers.models import Graph
from winkers.store import STORE_DIR

CONFIG_FILE = "config.json"

ENTRY_POINT_NAMES = {
    "app.py", "main.py", "manage.py", "wsgi.py", "asgi.py",
    "__main__.py", "server.py", "run.py",
}


def detect_entry_point(graph: Graph) -> str | None:
    """Find the most likely entry point file from the graph."""
    for rel in sorted(graph.files):
        name = rel.replace("\\", "/").split("/")[-1]
        if name in ENTRY_POINT_NAMES:
            return rel
    return None


def trace_startup_chain(graph: Graph, entry: str, max_depth: int = 2) -> list[str]:
    """Trace import edges from entry point up to max_depth levels deep.

    Returns a sorted list of file paths in the startup chain
    (including the entry point itself).
    """
    # Build adjacency: source_file -> set of target_files
    imports_from: dict[str, set[str]] = {}
    for edge in graph.import_edges:
        imports_from.setdefault(edge.source_file, set()).add(edge.target_file)

    chain: set[str] = {entry}
    frontier = {entry}

    for _ in range(max_depth):
        next_frontier: set[str] = set()
        for f in frontier:
            for target in imports_from.get(f, set()):
                if target not in chain:
                    chain.add(target)
                    next_frontier.add(target)
        frontier = next_frontier
        if not frontier:
            break

    return sorted(chain)


def save_protect_config(root: Path, entry: str, chain: list[str]) -> Path:
    """Save protect config to .winkers/config.json (merge with existing)."""
    config_path = root / STORE_DIR / CONFIG_FILE
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    config["protect"] = {
        "mode": "startup",
        "entry": entry,
        "chain": chain,
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path


def load_startup_chain(root: Path) -> set[str]:
    """Load the startup chain from config.json, or empty set."""
    config_path = root / STORE_DIR / CONFIG_FILE
    if not config_path.exists():
        return set()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return set(config.get("protect", {}).get("chain", []))
    except Exception:
        return set()
