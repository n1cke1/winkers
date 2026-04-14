"""Tests for winkers.detection.impact — signature diff and caller analysis."""

from winkers.detection.impact import (
    compute_diff,
    format_impact,
    snapshot_signatures,
)
from winkers.models import CallEdge, CallSite, FunctionNode, Graph, Param


def _make_graph() -> Graph:
    """Build a simple graph with known signatures and call edges."""
    graph = Graph()
    graph.functions = {
        "calc.py::add": FunctionNode(
            id="calc.py::add", file="calc.py", name="add",
            kind="function", language="python", line_start=1, line_end=3,
            params=[Param(name="a", type_hint="int"), Param(name="b", type_hint="int")],
            return_type="int",
        ),
        "calc.py::multiply": FunctionNode(
            id="calc.py::multiply", file="calc.py", name="multiply",
            kind="function", language="python", line_start=5, line_end=7,
            params=[Param(name="x", type_hint="int"), Param(name="y", type_hint="int")],
            return_type="int",
        ),
        "api.py::handler": FunctionNode(
            id="api.py::handler", file="api.py", name="handler",
            kind="function", language="python", line_start=1, line_end=5,
            params=[],
        ),
    }
    graph.call_edges = [
        CallEdge(
            source_fn="api.py::handler",
            target_fn="calc.py::add",
            call_site=CallSite(
                caller_fn_id="api.py::handler",
                file="api.py", line=3, expression="add(1, 2)",
            ),
        ),
    ]
    return graph


class TestSnapshotSignatures:
    def test_snapshot_all(self):
        graph = _make_graph()
        snap = snapshot_signatures(graph)
        assert "calc.py::add" in snap
        assert "calc.py::multiply" in snap
        assert "api.py::handler" in snap

    def test_snapshot_filtered(self):
        graph = _make_graph()
        snap = snapshot_signatures(graph, files=["calc.py"])
        assert "calc.py::add" in snap
        assert "api.py::handler" not in snap


class TestComputeDiff:
    def test_added_function(self):
        graph = _make_graph()
        old_sigs: dict[str, str] = {}  # no old functions in calc.py
        diff = compute_diff(old_sigs, graph, ["calc.py"])
        assert len(diff.added) == 2
        names = {fn.name for fn in diff.added}
        assert "add" in names
        assert "multiply" in names

    def test_removed_function(self):
        graph = _make_graph()
        old_sigs = {"calc.py::deleted_fn": "(x:int) -> int"}
        diff = compute_diff(old_sigs, graph, ["calc.py"])
        assert "calc.py::deleted_fn" in diff.removed

    def test_signature_change_with_callers(self):
        graph = _make_graph()
        old_sigs = {
            "calc.py::add": "(a:int, b:int) -> float",  # return type changed
            "calc.py::multiply": "(x:int, y:int) -> int",
        }
        diff = compute_diff(old_sigs, graph, ["calc.py"])
        assert len(diff.signature_changed) == 1
        sc = diff.signature_changed[0]
        assert sc.fn_id == "calc.py::add"
        assert len(sc.callers) == 1
        assert sc.callers[0].source_fn == "api.py::handler"

    def test_no_changes(self):
        graph = _make_graph()
        old_sigs = snapshot_signatures(graph, ["calc.py"])
        diff = compute_diff(old_sigs, graph, ["calc.py"])
        assert diff.added == []
        assert diff.removed == []
        assert diff.signature_changed == []


class TestFormatImpact:
    def test_format_with_changes(self):
        graph = _make_graph()
        old_sigs = {"calc.py::add": "(a:int, b:int) -> float"}
        diff = compute_diff(old_sigs, graph, ["calc.py"])
        result = format_impact(diff)
        assert "added" in result  # multiply is "new" since not in old_sigs
        assert "signature_changes" in result
        sc = result["signature_changes"][0]
        assert sc["function"] == "add"
        assert sc["callers_count"] == 1

    def test_format_empty_diff(self):
        graph = _make_graph()
        old_sigs = snapshot_signatures(graph, ["calc.py"])
        diff = compute_diff(old_sigs, graph, ["calc.py"])
        result = format_impact(diff)
        assert result == {}
