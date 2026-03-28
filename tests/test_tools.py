"""Unit tests for MCP tool implementations."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.mcp.tools import (
    _section_functions_graph,
    _section_hotspots,
    _section_map,
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
# map section
# ---------------------------------------------------------------------------

def test_map_has_zones(graph, tmp_path):
    result = _section_map(graph, None, tmp_path)
    assert "zones" in result
    assert len(result["zones"]) >= 1
    assert all("name" in z for z in result["zones"])
    assert all("files" in z for z in result["zones"])
    assert all("functions" in z for z in result["zones"])


def test_map_has_hotspots(graph, tmp_path):
    result = _section_map(graph, None, tmp_path)
    assert "hotspots_top5" in result
    for h in result["hotspots_top5"]:
        assert "locked" in h
        assert "fn" in h
        assert "call_count" in h


def test_map_total_counts(graph, tmp_path):
    result = _section_map(graph, None, tmp_path)
    assert result["total_files"] == len(graph.files)
    assert result["total_functions"] == len(graph.functions)


def test_map_zone_filter(graph, tmp_path):
    result = _section_map(graph, "modules", tmp_path)
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


def test_scope_function_callers_constraint(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    assert "callers_constraint" in result
    assert "safe_changes" in result["callers_constraint"]
    assert "breaking_changes" in result["callers_constraint"]
    assert "callers_expect" in result["callers_constraint"]


def test_scope_function_free(graph):
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
# functions_graph section
# ---------------------------------------------------------------------------

def test_functions_graph_indexed(graph):
    result = _section_functions_graph(graph, None)
    assert "functions" in result
    assert "total" in result
    for key in result["functions"]:
        assert key.isdigit()


def test_functions_graph_callers_are_indices(graph):
    result = _section_functions_graph(graph, None)
    all_indices = set(result["functions"].keys())
    for entry in result["functions"].values():
        if "callers" in entry:
            for caller_idx in entry["callers"]:
                assert str(caller_idx) in all_indices


def test_functions_graph_zone_filter(graph):
    result = _section_functions_graph(graph, "modules")
    for entry in result["functions"].values():
        assert "modules" in entry["file"]


# ---------------------------------------------------------------------------
# hotspots section
# ---------------------------------------------------------------------------

def test_hotspots_default_threshold(graph):
    result = _section_hotspots(graph, 10)
    assert "hotspots" in result
    assert result["min_callers"] == 10
    assert isinstance(result["count"], int)


def test_hotspots_low_threshold(graph):
    result = _section_hotspots(graph, 1)
    assert result["count"] >= 1
    for h in result["hotspots"]:
        assert "function" in h
        assert "callers" in h
        assert len(h["callers"]) >= 1
        assert "expression" in h["callers"][0]
