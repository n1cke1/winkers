"""Tests for the find_work_area MCP tool — Phase 2 description-first search."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

import winkers.embeddings.builder as eb
import winkers.mcp.tools as tools_mod
from winkers.descriptions.store import UnitsStore
from winkers.embeddings.builder import (
    INDEX_FILENAME,
    embed_units,
    save_index,
)
from winkers.models import (
    FileNode,
    FunctionNode,
    Graph,
    Param,
)


class _StubModel:
    """Deterministic model for unit tests — same text → same vector."""

    def encode(self, texts, **kwargs):
        out = np.zeros((len(texts), eb.DIMENSION), dtype=np.float32)
        for i, t in enumerate(texts):
            h = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
            np.random.seed(h % (2**32))
            v = np.random.randn(eb.DIMENSION).astype(np.float32)
            v /= np.linalg.norm(v)
            out[i] = v
        return out


@pytest.fixture(autouse=True)
def stub_model(monkeypatch):
    monkeypatch.setattr(eb, "_MODEL", _StubModel())
    yield


def _make_graph_with_fn(fn_id: str, file: str, name: str,
                       line_start: int = 10, line_end: int = 50) -> Graph:
    g = Graph()
    g.functions[fn_id] = FunctionNode(
        id=fn_id, file=file, name=name,
        kind="function", language="python",
        line_start=line_start, line_end=line_end,
        params=[Param(name="x")],
    )
    g.files[file] = FileNode(
        path=file, language="python",
        imports=[], function_ids=[fn_id],
    )
    return g


def _populate_units(root: Path, units: list[dict]) -> None:
    UnitsStore(root).save(units)
    idx, _ = embed_units(units)
    save_index(idx, root / ".winkers" / INDEX_FILENAME)


# ---------------------------------------------------------------------------
# Error / empty paths
# ---------------------------------------------------------------------------

def test_missing_index_returns_hint(tmp_path):
    """Index never built → tool returns an error with a clear hint."""
    g = Graph()
    out = tools_mod._tool_find_work_area(g, {"query": "anything"}, tmp_path)
    assert "error" in out
    assert "winkers init --with-units" in out["hint"]


def test_empty_query_rejected(tmp_path):
    g = Graph()
    out = tools_mod._tool_find_work_area(g, {"query": "  "}, tmp_path)
    assert out == {"error": "query required"}


def test_missing_query_rejected(tmp_path):
    g = Graph()
    out = tools_mod._tool_find_work_area(g, {}, tmp_path)
    assert out == {"error": "query required"}


# ---------------------------------------------------------------------------
# Match shaping
# ---------------------------------------------------------------------------

def test_function_unit_match_includes_line_numbers(tmp_path):
    """function_units get file + line_start + line_end joined from graph."""
    fn_id = "engine/x.py::foo"
    g = _make_graph_with_fn(fn_id, "engine/x.py", "foo",
                            line_start=10, line_end=50)
    units = [
        {
            "id": fn_id,
            "kind": "function_unit",
            "name": "foo",
            "description": "does the foo thing",
            "anchor": {"file": "engine/x.py", "fn": "foo"},
        },
    ]
    _populate_units(tmp_path, units)

    out = tools_mod._tool_find_work_area(g, {"query": "foo"}, tmp_path)
    m = out["matches"][0]
    assert m["id"] == fn_id
    assert m["file"] == "engine/x.py"
    assert m["line_start"] == 10
    assert m["line_end"] == 50


def test_traceability_unit_resolves_source_anchors(tmp_path):
    """Traceability unit with source_anchors → each anchor gets line ranges
    when the anchor id matches a graph function."""
    fn_id = "engine/x.py::foo"
    g = _make_graph_with_fn(fn_id, "engine/x.py", "foo",
                            line_start=5, line_end=20)
    units = [
        {
            "id": "concept_X",
            "kind": "traceability_unit",
            "name": "X concept",
            "source_files": ["engine/x.py"],
            "source_anchors": [fn_id, "engine/x.py::missing_fn"],
            "description": "concept X involves foo",
        },
    ]
    _populate_units(tmp_path, units)

    out = tools_mod._tool_find_work_area(g, {"query": "X concept"}, tmp_path)
    m = out["matches"][0]
    assert m["kind"] == "traceability_unit"
    anchors = m["source_anchors"]
    # Anchor that resolves: has line_start
    resolved = next(a for a in anchors if a["id"] == fn_id)
    assert resolved["file"] == "engine/x.py"
    assert resolved["line_start"] == 5
    # Anchor that doesn't resolve: only has id
    unresolved = next(a for a in anchors if a["id"] == "engine/x.py::missing_fn")
    assert "line_start" not in unresolved


def test_route_attached_for_route_handlers(tmp_path):
    """function_unit that's a route handler exposes route info in the response."""
    fn_id = "app.py::api_calc"
    g = Graph()
    g.functions[fn_id] = FunctionNode(
        id=fn_id, file="app.py", name="api_calc",
        kind="function", language="python",
        line_start=100, line_end=120, params=[],
        route="/api/calc", http_method="POST",
    )
    g.files["app.py"] = FileNode(
        path="app.py", language="python",
        imports=[], function_ids=[fn_id],
    )
    units = [{
        "id": fn_id, "kind": "function_unit", "name": "api_calc",
        "description": "calc endpoint",
        "anchor": {"file": "app.py", "fn": "api_calc"},
    }]
    _populate_units(tmp_path, units)
    out = tools_mod._tool_find_work_area(g, {"query": "calc"}, tmp_path)
    assert out["matches"][0]["route"] == "POST /api/calc"


def test_description_truncated(tmp_path):
    """Long descriptions are clipped to keep the tool response tight."""
    long_desc = "word " * 100  # ~500 chars
    fn_id = "x.py::big"
    g = _make_graph_with_fn(fn_id, "x.py", "big")
    units = [{
        "id": fn_id, "kind": "function_unit", "name": "big",
        "description": long_desc,
        "anchor": {"file": "x.py", "fn": "big"},
    }]
    _populate_units(tmp_path, units)
    out = tools_mod._tool_find_work_area(g, {"query": "big"}, tmp_path)
    desc = out["matches"][0]["description"]
    assert len(desc) <= 250
    assert desc.endswith("...")


# ---------------------------------------------------------------------------
# Threshold logic — score-driven verdict
# ---------------------------------------------------------------------------

def _force_top_score(monkeypatch, score: float, gap: float = 0.1):
    """Patch search() to return a controlled top score for verdict tests."""
    def fake_search(index, query, k=5):
        return [
            (score, "u1"),
            (score - gap, "u2"),
            (score - gap - 0.01, "u3"),
            (score - gap - 0.02, "u4"),
            (score - gap - 0.03, "u5"),
        ][:k]
    monkeypatch.setattr(tools_mod, "search", fake_search, raising=False)
    # The import inside _tool_find_work_area uses fresh import; patch the
    # winkers.embeddings module too.
    import winkers.embeddings as wemb
    monkeypatch.setattr(wemb, "search", fake_search)


def test_verdict_ok_high_above_hard_threshold(tmp_path, monkeypatch):
    g = Graph()
    units = [{"id": f"u{i}", "kind": "function_unit", "name": f"u{i}",
              "description": "x", "anchor": {"file": "f.py", "fn": f"u{i}"}}
             for i in range(1, 6)]
    _populate_units(tmp_path, units)
    _force_top_score(monkeypatch, score=0.7)
    out = tools_mod._tool_find_work_area(g, {"query": "x"}, tmp_path)
    assert out["verdict"] == "OK"
    assert out["confidence"] == "high"


def test_verdict_ok_medium_with_clear_leader(tmp_path, monkeypatch):
    """Score in [0.45, 0.55) but big gap top↔bottom → still OK (medium)."""
    g = Graph()
    units = [{"id": f"u{i}", "kind": "function_unit", "name": f"u{i}",
              "description": "x", "anchor": {"file": "f.py", "fn": f"u{i}"}}
             for i in range(1, 6)]
    _populate_units(tmp_path, units)
    _force_top_score(monkeypatch, score=0.50, gap=0.10)
    out = tools_mod._tool_find_work_area(g, {"query": "x"}, tmp_path)
    assert out["verdict"] == "OK"
    assert out["confidence"] == "medium"


def test_verdict_no_match_below_floor(tmp_path, monkeypatch):
    g = Graph()
    units = [{"id": f"u{i}", "kind": "function_unit", "name": f"u{i}",
              "description": "x", "anchor": {"file": "f.py", "fn": f"u{i}"}}
             for i in range(1, 6)]
    _populate_units(tmp_path, units)
    _force_top_score(monkeypatch, score=0.40)
    out = tools_mod._tool_find_work_area(g, {"query": "x"}, tmp_path)
    assert out["verdict"] == "NO_CLEAR_MATCH"
    assert out["confidence"] == "low"


def test_verdict_no_match_in_middle_without_gap(tmp_path, monkeypatch):
    """Score in [0.45, 0.55) but clustered top-5 (no clear leader) → NO_CLEAR_MATCH."""
    g = Graph()
    units = [{"id": f"u{i}", "kind": "function_unit", "name": f"u{i}",
              "description": "x", "anchor": {"file": "f.py", "fn": f"u{i}"}}
             for i in range(1, 6)]
    _populate_units(tmp_path, units)
    _force_top_score(monkeypatch, score=0.50, gap=0.01)
    out = tools_mod._tool_find_work_area(g, {"query": "x"}, tmp_path)
    assert out["verdict"] == "NO_CLEAR_MATCH"


# ---------------------------------------------------------------------------
# Stale link safety
# ---------------------------------------------------------------------------

def test_stale_vector_id_returns_warning(tmp_path):
    """If embeddings have an id no longer in units.json, surface a warning."""
    g = Graph()
    # Build embeddings for u1 only.
    units_for_index = [{"id": "u1", "name": "x", "description": "desc"}]
    idx, _ = embed_units(units_for_index)
    save_index(idx, tmp_path / ".winkers" / INDEX_FILENAME)
    # But save units.json with a DIFFERENT unit (orphan in opposite direction).
    UnitsStore(tmp_path).save([
        {"id": "u2", "kind": "function_unit", "description": "x"},
    ])
    out = tools_mod._tool_find_work_area(g, {"query": "x"}, tmp_path)
    m = out["matches"][0]
    assert m["id"] == "u1"
    assert "warning" in m
