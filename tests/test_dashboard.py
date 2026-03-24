"""Tests for dashboard HTTP API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from winkers.dashboard.api import _graph_to_cytoscape, _history, _preview, create_app
from winkers.models import CallEdge, CallSite, FunctionNode, Graph, ImportEdge

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fn(fn_id: str, file: str = "api/a.py"):
    return FunctionNode(
        id=fn_id, file=file, name=fn_id.split("::")[-1],
        kind="function", language="python",
        line_start=1, line_end=10, params=[],
    )


def _edge(src: str, tgt: str):
    return CallEdge(
        source_fn=src, target_fn=tgt,
        call_site=CallSite(caller_fn_id=src, file="api/a.py", line=5, expression=f"{tgt}()"),
        confidence=0.9,
    )


def _graph_with_data():
    g = Graph()
    g.functions["api/a.py::foo"] = _fn("api/a.py::foo", "api/a.py")
    g.functions["db/b.py::bar"] = _fn("db/b.py::bar", "db/b.py")
    g.call_edges = [_edge("api/a.py::foo", "db/b.py::bar")]
    g.import_edges = [ImportEdge(source_file="api/a.py", target_file="db/b.py", names=[])]
    return g


# ---------------------------------------------------------------------------
# Unit tests (no HTTP)
# ---------------------------------------------------------------------------

class TestGraphToCytoscape:
    def test_returns_nodes_and_edges(self):
        g = _graph_with_data()
        result = _graph_to_cytoscape(g)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1

    def test_node_has_required_fields(self):
        g = _graph_with_data()
        result = _graph_to_cytoscape(g)
        node = result["nodes"][0]["data"]
        for field in ("id", "label", "zone", "locked", "callers", "kind"):
            assert field in node

    def test_zone_filter(self):
        g = _graph_with_data()
        result = _graph_to_cytoscape(g, zone_filter="api")
        ids = [n["data"]["id"] for n in result["nodes"]]
        assert "api/a.py::foo" in ids
        assert "db/b.py::bar" not in ids

    def test_edge_has_confidence(self):
        g = _graph_with_data()
        result = _graph_to_cytoscape(g)
        assert result["edges"][0]["data"]["confidence"] == 0.9


class TestPreview:
    def test_known_function(self):
        g = _graph_with_data()
        result = _preview(g, "api/a.py::foo")
        assert result["target"] == "api/a.py::foo"
        assert "api/a.py::foo" in result["highlight_target"]
        assert isinstance(result["highlight_neighbors"], list)

    def test_lookup_by_name(self):
        g = _graph_with_data()
        result = _preview(g, "foo")
        assert result["target"] == "api/a.py::foo"

    def test_unknown_function(self):
        g = _graph_with_data()
        result = _preview(g, "nonexistent")
        assert "error" in result

    def test_locked_function_in_preview(self):
        g = _graph_with_data()
        # bar is the target of foo → bar is locked
        result = _preview(g, "db/b.py::bar")
        assert result["target"] == "db/b.py::bar"
        # foo should be in neighbors (it calls bar)
        assert "api/a.py::foo" in result["highlight_neighbors"]


class TestHistory:
    def test_empty_history(self, tmp_path):
        result = _history(tmp_path)
        assert result == []

    def test_reads_snapshots(self, tmp_path):
        history_dir = tmp_path / ".winkers" / "history"
        history_dir.mkdir(parents=True)
        snap = {
            "files": {}, "functions": {"a::b": {}}, "call_edges": [{}, {}],
            "import_edges": [], "meta": {}
        }
        (history_dir / "2026-03-21T12-00-00.json").write_text(
            json.dumps(snap), encoding="utf-8"
        )
        result = _history(tmp_path)
        assert len(result) == 1
        assert result[0]["functions"] == 1
        assert result[0]["call_edges"] == 2

    def test_ignores_malformed(self, tmp_path):
        history_dir = tmp_path / ".winkers" / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "bad.json").write_text("not json", encoding="utf-8")
        result = _history(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# HTTP tests
# ---------------------------------------------------------------------------

@pytest.fixture
def graph_project(tmp_path):
    """Set up a project with a saved graph."""
    store_dir = tmp_path / ".winkers"
    store_dir.mkdir()
    g = _graph_with_data()
    (store_dir / "graph.json").write_text(g.model_dump_json(), encoding="utf-8")
    return tmp_path


@pytest.fixture
async def client(graph_project):
    app = create_app(graph_project)
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.mark.asyncio
async def test_get_graph(client):
    resp = await client.get("/api/graph")
    assert resp.status == 200
    data = await resp.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2


@pytest.mark.asyncio
async def test_get_graph_zone_filter(client):
    resp = await client.get("/api/graph?zone=api")
    assert resp.status == 200
    data = await resp.json()
    assert all(n["data"]["zone"] == "api" for n in data["nodes"])


@pytest.mark.asyncio
async def test_get_preview(client):
    resp = await client.get("/api/preview?fn=foo")
    assert resp.status == 200
    data = await resp.json()
    assert "target" in data
    assert data["target"] == "api/a.py::foo"


@pytest.mark.asyncio
async def test_get_preview_unknown(client):
    resp = await client.get("/api/preview?fn=doesnotexist")
    assert resp.status == 200
    data = await resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_get_history_empty(client):
    resp = await client.get("/api/history")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_index_html(client):
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "cytoscape" in text.lower()
    assert "Winkers" in text


@pytest.mark.asyncio
async def test_no_graph_returns_404(tmp_path):
    """API returns 404 when graph.json doesn't exist."""
    app = create_app(tmp_path)
    async with TestClient(TestServer(app)) as c:
        resp = await c.get("/api/graph")
        assert resp.status == 404
