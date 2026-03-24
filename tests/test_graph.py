"""Tests for GraphBuilder."""

from pathlib import Path

from winkers.graph import GraphBuilder

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "python_project"


def test_build_finds_files():
    builder = GraphBuilder()
    graph = builder.build(FIXTURE_DIR)
    assert len(graph.files) >= 2


def test_build_finds_functions():
    builder = GraphBuilder()
    graph = builder.build(FIXTURE_DIR)
    names = [fn.name for fn in graph.functions.values()]
    assert "calculate_price" in names
    assert "check_stock" in names


def test_function_has_correct_params():
    builder = GraphBuilder()
    graph = builder.build(FIXTURE_DIR)
    fn = next(f for f in graph.functions.values() if f.name == "calculate_price")
    param_names = [p.name for p in fn.params]
    assert "item_id" in param_names
    assert "qty" in param_names


def test_meta_populated():
    builder = GraphBuilder()
    graph = builder.build(FIXTURE_DIR)
    assert "python" in graph.meta.get("languages", [])
    assert graph.meta["total_functions"] > 0
