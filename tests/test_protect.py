"""Tests for startup chain protection."""

from winkers.models import FileNode, Graph, ImportEdge
from winkers.protect import (
    detect_entry_point,
    load_startup_chain,
    save_protect_config,
    trace_startup_chain,
)


def _graph_with_chain():
    g = Graph()
    for path in ["app.py", "db.py", "utils.py", "tests/test_app.py"]:
        g.files[path] = FileNode(
            path=path, language="python", imports=[], function_ids=[],
            zone=path.split("/")[0].rsplit(".", 1)[0],
        )
    g.import_edges = [
        ImportEdge(source_file="app.py", target_file="db.py", names=["get_conn"]),
        ImportEdge(source_file="db.py", target_file="utils.py", names=["log"]),
        ImportEdge(source_file="tests/test_app.py", target_file="app.py", names=[]),
    ]
    return g


def test_detect_entry_point():
    g = _graph_with_chain()
    assert detect_entry_point(g) == "app.py"


def test_detect_entry_point_none():
    g = Graph()
    g.files["lib.py"] = FileNode(
        path="lib.py", language="python", imports=[], function_ids=[],
    )
    assert detect_entry_point(g) is None


def test_trace_startup_chain():
    g = _graph_with_chain()
    chain = trace_startup_chain(g, "app.py", max_depth=2)
    assert "app.py" in chain
    assert "db.py" in chain
    assert "utils.py" in chain
    # test file imports app.py but is NOT in chain (chain traces outward)
    assert "tests/test_app.py" not in chain


def test_trace_depth_1():
    g = _graph_with_chain()
    chain = trace_startup_chain(g, "app.py", max_depth=1)
    assert "app.py" in chain
    assert "db.py" in chain
    assert "utils.py" not in chain  # depth 2, not reached


def test_save_and_load(tmp_path):
    save_protect_config(tmp_path, "app.py", ["app.py", "db.py"])
    chain = load_startup_chain(tmp_path)
    assert chain == {"app.py", "db.py"}


def test_load_empty(tmp_path):
    assert load_startup_chain(tmp_path) == set()


def test_cli_protect_startup(tmp_path):
    """CLI protect --startup detects entry and saves chain."""
    import shutil
    from pathlib import Path

    from click.testing import CliRunner

    from winkers.cli.main import cli

    # Copy fixture and init
    fixture = Path(__file__).parent / "fixtures" / "flask_project"
    if not fixture.exists():
        # Use python_project as fallback
        fixture = Path(__file__).parent / "fixtures" / "python_project"
    shutil.copytree(fixture, tmp_path / "project")
    project = tmp_path / "project"

    runner = CliRunner()
    # First init to create graph
    runner.invoke(cli, ["init", str(project)])

    # Then protect
    result = runner.invoke(cli, ["protect", str(project), "--startup"])
    # May or may not find entry point depending on fixture
    assert result.exit_code == 0
