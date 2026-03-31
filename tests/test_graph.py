"""Tests for GraphBuilder."""

from pathlib import Path

from winkers.graph import GraphBuilder, _find_passthrough_prefixes

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


# ---------------------------------------------------------------------------
# Passthrough prefix detection
# ---------------------------------------------------------------------------

def test_passthrough_detects_deep_layout():
    paths = [
        "src/winkers/mcp/tools.py",
        "src/winkers/graph.py",
        "src/winkers/models.py",
        "tests/test_graph.py",
    ]
    prefixes = _find_passthrough_prefixes(paths)
    assert prefixes["src"] == "src/winkers/"
    assert "tests" not in prefixes


def test_passthrough_flat_layout_no_prefix():
    paths = [
        "api/prices.py",
        "modules/pricing.py",
        "models.py",
    ]
    prefixes = _find_passthrough_prefixes(paths)
    assert prefixes == {}


def test_zones_flat_project():
    builder = GraphBuilder()
    graph = builder.build(FIXTURE_DIR)
    zones = {f.zone for f in graph.files.values()}
    assert "modules" in zones
    assert "api" in zones


def test_zones_deep_project_skips_passthrough():
    """Simulate a deep layout: src/pkg/ should be stripped."""
    from winkers.models import FileNode, Graph

    graph = Graph()
    graph.files = {
        "src/pkg/mcp/tools.py": FileNode(
            path="src/pkg/mcp/tools.py", language="python",
            imports=[], function_ids=[],
        ),
        "src/pkg/graph.py": FileNode(
            path="src/pkg/graph.py", language="python",
            imports=[], function_ids=[],
        ),
        "tests/test_graph.py": FileNode(
            path="tests/test_graph.py", language="python",
            imports=[], function_ids=[],
        ),
    }
    builder = GraphBuilder()
    builder._assign_zones(graph)

    assert graph.files["src/pkg/mcp/tools.py"].zone == "mcp"
    assert graph.files["src/pkg/graph.py"].zone == "core"
    assert graph.files["tests/test_graph.py"].zone == "tests"
