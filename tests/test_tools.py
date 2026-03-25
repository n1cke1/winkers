"""Unit tests for MCP tool implementations."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.mcp.tools import (
    _tool_functions_graph,
    _tool_hotspots,
    _tool_map,
    _tool_scope,
)
from winkers.resolver import CrossFileResolver

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

def test_map_has_zones(graph):
    result = _tool_map(graph, {})
    assert "zones" in result
    assert len(result["zones"]) >= 1
    assert all("name" in z for z in result["zones"])
    assert all("files" in z for z in result["zones"])
    assert all("functions" in z for z in result["zones"])


def test_map_has_hotspots(graph):
    result = _tool_map(graph, {})
    assert "hotspots" in result
    for h in result["hotspots"]:
        assert "locked" in h
        assert "fn" in h
        assert "call_count" in h


def test_map_total_counts(graph):
    result = _tool_map(graph, {})
    assert result["total_files"] == len(graph.files)
    assert result["total_functions"] == len(graph.functions)


def test_map_zone_filter(graph):
    result = _tool_map(graph, {"zone": "modules"})
    assert "zones" in result
    assert all(z["name"] == "modules" for z in result["zones"])


# ---------------------------------------------------------------------------
# scope — function
# ---------------------------------------------------------------------------

def test_scope_function_locked(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    assert "function" in result
    assert result["function"]["locked"] is True
    assert len(result["callers"]) >= 2


def test_scope_function_callers_have_fields(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    for c in result["callers"]:
        assert "fn" in c
        assert "file" in c
        assert "line" in c
        assert "expression" in c
        assert "confidence" in c


def test_scope_function_constraints(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    assert "constraints" in result
    assert "safe_changes" in result["constraints"]
    assert "breaking_changes" in result["constraints"]
    assert "callers_expect" in result["constraints"]


def test_scope_function_free(graph):
    # reserve_items has no callers at all
    result = _tool_scope(graph, {"function": "modules/inventory.py::reserve_items"})
    assert result["function"]["locked"] is False
    assert len(result["callers"]) == 0


def test_scope_function_by_short_name(graph):
    result = _tool_scope(graph, {"function": "calculate_price"})
    assert "function" in result


def test_scope_function_not_found(graph):
    result = _tool_scope(graph, {"function": "nonexistent::fn"})
    assert "error" in result


# ---------------------------------------------------------------------------
# scope — file
# ---------------------------------------------------------------------------

def test_scope_file(graph):
    result = _tool_scope(graph, {"file": "modules/pricing.py"})
    assert "functions" in result
    assert len(result["functions"]) >= 3


def test_scope_file_fields(graph):
    result = _tool_scope(graph, {"file": "modules/pricing.py"})
    assert "language" in result
    assert "loc" in result
    for fn in result["functions"]:
        assert "locked" in fn
        assert "callers" in fn


def test_scope_no_args(graph):
    result = _tool_scope(graph, {})
    assert "error" in result


# ---------------------------------------------------------------------------
# functions_graph
# ---------------------------------------------------------------------------

def test_functions_graph_indexed(graph):
    result = _tool_functions_graph(graph)
    assert "functions" in result
    assert "total" in result
    # All keys are numeric strings
    for key in result["functions"]:
        assert key.isdigit()


def test_functions_graph_callers_are_indices(graph):
    result = _tool_functions_graph(graph)
    all_indices = set(result["functions"].keys())
    for entry in result["functions"].values():
        if "callers" in entry:
            for caller_idx in entry["callers"]:
                assert str(caller_idx) in all_indices



# ---------------------------------------------------------------------------
# hotspots
# ---------------------------------------------------------------------------

def test_hotspots_default_threshold(graph):
    result = _tool_hotspots(graph, {})
    assert "hotspots" in result
    assert result["min_callers"] == 10
    # Our test fixture has small functions, likely 0 results at threshold 10
    assert isinstance(result["count"], int)


def test_hotspots_low_threshold(graph):
    result = _tool_hotspots(graph, {"min_callers": 1})
    assert result["count"] >= 1
    for h in result["hotspots"]:
        assert "function" in h
        assert "callers" in h
        assert len(h["callers"]) >= 1
        assert "expression" in h["callers"][0]


