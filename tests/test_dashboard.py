"""Tests for dashboard HTTP API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from winkers.dashboard.api import create_app
from winkers.dashboard.handlers.data import _history
from winkers.dashboard.handlers.graph import _graph_to_cytoscape, _preview
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


# ---------------------------------------------------------------------------
# Sessions & Insights endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_sessions_empty(client):
    resp = await client.get("/api/sessions")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_get_sessions_with_data(graph_project):
    """Sessions endpoint returns recorded sessions."""
    from winkers.models import ScoredSession, SessionRecord
    from winkers.session_store import SessionStore

    store = SessionStore(graph_project)
    session = SessionRecord(
        session_id="dash-s1",
        started_at="2026-03-25T10:00:00Z",
        completed_at="2026-03-25T10:30:00Z",
        task_prompt="Add payment feature",
        task_hash="abc123",
        total_turns=8,
        exploration_turns=4,
        modification_turns=3,
        verification_turns=1,
    )
    store.save(ScoredSession(session=session, score=0.82))

    app = create_app(graph_project)
    async with TestClient(TestServer(app)) as c:
        resp = await c.get("/api/sessions")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["session_id"] == "dash-s1"
        assert data[0]["score"] == pytest.approx(0.82)
        assert data[0]["score_label"] == "good"
        assert data[0]["total_turns"] == 8


@pytest.mark.asyncio
async def test_get_insights_empty(client):
    resp = await client.get("/api/insights")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_get_insights_with_data(graph_project):
    """Insights endpoint returns open insights."""
    from winkers.analyzer import AnalysisResult, Insight
    from winkers.insights_store import InsightsStore

    store = InsightsStore(graph_project)
    store.merge(AnalysisResult(
        session_id="s1",
        insights=[
            Insight(
                category="CONSTRAINT",
                description="tax per line item",
                semantic_target="constraints",
                injection_content="Tax must be per line item.",
                priority="high",
                turns_wasted=4,
                tokens_wasted=6000,
                session_id="s1",
            ),
        ],
    ))

    app = create_app(graph_project)
    async with TestClient(TestServer(app)) as c:
        resp = await c.get("/api/insights")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["category"] == "CONSTRAINT"
        assert data[0]["priority"] == "high"
        assert data[0]["turns_wasted"] == 4


@pytest.mark.asyncio
async def test_index_has_learning_tab(client):
    resp = await client.get("/")
    text = await resp.text()
    assert "Agent Learning" in text
    assert "session-select" in text


@pytest.mark.asyncio
async def test_get_tool_stats_no_sessions(client):
    """Without recorded sessions, returns only estimated entries."""
    resp = await client.get("/api/tool-stats")
    assert resp.status == 200
    data = await resp.json()
    # Graph exists so we get estimates, but no recorded entries
    assert all(d["source"] == "estimated" for d in data)
    assert all(d["calls"] == 0 for d in data)


@pytest.mark.asyncio
async def test_get_tool_stats_with_data(graph_project):
    """Tool stats aggregates token usage across sessions."""
    from winkers.models import ScoredSession, SessionRecord, ToolCall
    from winkers.session_store import SessionStore

    store = SessionStore(graph_project)
    session = SessionRecord(
        session_id="ts-s1",
        started_at="2026-03-25T10:00:00Z",
        completed_at="2026-03-25T10:30:00Z",
        task_hash="abc",
        tool_calls=[
            ToolCall(name="Read", tokens_in=500, tokens_out=20),
            ToolCall(name="Read", tokens_in=600, tokens_out=25),
            ToolCall(name="Edit", tokens_in=800, tokens_out=50),
            ToolCall(name="mcp__winkers__orient", tokens_in=300, tokens_out=200),
        ],
    )
    store.save(ScoredSession(session=session))

    app = create_app(graph_project)
    async with TestClient(TestServer(app)) as c:
        resp = await c.get("/api/tool-stats")
        assert resp.status == 200
        data = await resp.json()

        by_name = {d["name"]: d for d in data}
        assert by_name["Read"]["calls"] == 2
        assert by_name["Read"]["tokens_in"] == 1100
        assert by_name["Read"]["source"] == "recorded"
        assert by_name["Edit"]["calls"] == 1
        assert by_name["mcp__winkers__orient"]["calls"] == 1
        # Recorded orient tool should have estimated_out from graph
        assert "estimated_out" in by_name["mcp__winkers__orient"]


@pytest.mark.asyncio
async def test_get_tool_stats_estimated_only(graph_project):
    """Without sessions, tool stats returns estimates from graph."""
    app = create_app(graph_project)
    async with TestClient(TestServer(app)) as c:
        resp = await c.get("/api/tool-stats")
        assert resp.status == 200
        data = await resp.json()
        # Should have estimated entries from graph
        assert len(data) > 0
        assert all(d["source"] == "estimated" for d in data)
        names = {d["name"] for d in data}
        assert "mcp__winkers__orient" in names
        assert "mcp__winkers__scope" in names
        for d in data:
            assert d["estimated_out"] > 0
