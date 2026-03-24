"""Tests for Pydantic models and Graph computed methods."""

import json

from winkers.models import (
    CallEdge,
    CallSite,
    FileNode,
    FunctionNode,
    Graph,
    ImportEdge,
    Param,
)


def _make_fn(fn_id: str, file: str = "a.py") -> FunctionNode:
    return FunctionNode(
        id=fn_id,
        file=file,
        name=fn_id.split("::")[-1],
        kind="function",
        language="python",
        line_start=1,
        line_end=5,
        params=[],
    )


def _make_edge(source: str, target: str, file: str = "a.py", line: int = 1) -> CallEdge:
    return CallEdge(
        source_fn=source,
        target_fn=target,
        call_site=CallSite(
            caller_fn_id=source,
            file=file,
            line=line,
            expression=f"{target.split('::')[-1]}()",
        ),
        confidence=1.0,
    )


# --- Model creation ---

def test_param_minimal():
    p = Param(name="x")
    assert p.name == "x"
    assert p.type_hint is None


def test_function_node_minimal():
    fn = _make_fn("a.py::foo")
    assert fn.id == "a.py::foo"
    assert fn.is_async is False


def test_file_node_minimal():
    f = FileNode(path="a.py", language="python", imports=[], function_ids=[])
    assert f.lines_of_code == 0


def test_call_edge_confidence_default():
    edge = _make_edge("a.py::foo", "b.py::bar")
    assert edge.confidence == 1.0


def test_import_edge():
    e = ImportEdge(source_file="a.py", target_file="b.py", names=["bar"])
    assert e.names == ["bar"]


# --- Graph computed methods ---

def test_is_locked_true():
    graph = Graph(
        functions={"a.py::foo": _make_fn("a.py::foo"), "b.py::bar": _make_fn("b.py::bar", "b.py")},
        call_edges=[_make_edge("a.py::foo", "b.py::bar")],
    )
    assert graph.is_locked("b.py::bar") is True


def test_is_locked_false():
    graph = Graph(functions={"a.py::foo": _make_fn("a.py::foo")})
    assert graph.is_locked("a.py::foo") is False


def test_callers():
    graph = Graph(
        call_edges=[
            _make_edge("a.py::foo", "c.py::baz"),
            _make_edge("b.py::bar", "c.py::baz"),
        ]
    )
    result = graph.callers("c.py::baz")
    assert len(result) == 2
    assert all(e.target_fn == "c.py::baz" for e in result)


def test_callees():
    graph = Graph(
        call_edges=[
            _make_edge("a.py::foo", "b.py::bar"),
            _make_edge("a.py::foo", "c.py::baz"),
        ]
    )
    result = graph.callees("a.py::foo")
    assert len(result) == 2
    assert all(e.source_fn == "a.py::foo" for e in result)


def test_locked_functions():
    fn_foo = _make_fn("a.py::foo")
    fn_bar = _make_fn("b.py::bar", "b.py")
    fn_baz = _make_fn("c.py::baz", "c.py")
    graph = Graph(
        functions={"a.py::foo": fn_foo, "b.py::bar": fn_bar, "c.py::baz": fn_baz},
        call_edges=[
            _make_edge("a.py::foo", "b.py::bar"),
            _make_edge("a.py::foo", "c.py::baz"),
        ],
    )
    locked = graph.locked_functions()
    locked_ids = {fn.id for fn in locked}
    assert "b.py::bar" in locked_ids
    assert "c.py::baz" in locked_ids
    assert "a.py::foo" not in locked_ids


# --- JSON round-trip ---

def test_graph_json_roundtrip():
    fn = _make_fn("a.py::foo")
    edge = _make_edge("a.py::foo", "b.py::bar")
    graph = Graph(
        functions={"a.py::foo": fn},
        call_edges=[edge],
        meta={"languages": ["python"]},
    )
    data = json.loads(graph.model_dump_json())
    restored = Graph.model_validate(data)
    assert restored.model_dump() == graph.model_dump()
