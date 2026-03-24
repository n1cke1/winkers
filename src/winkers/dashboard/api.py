"""Dashboard HTTP + WebSocket server for Winkers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from winkers.store import GraphStore


def _graph_to_cytoscape(graph, zone_filter: str | None = None) -> dict[str, Any]:
    """Convert Graph to Cytoscape.js elements format."""
    from winkers.mcp.tools import _infer_zone

    nodes = []
    edges = []

    fn_ids_in_zone: set[str] = set()

    for fn_id, fn in graph.functions.items():
        zone = _infer_zone(fn.file)
        if zone_filter and zone != zone_filter:
            continue
        fn_ids_in_zone.add(fn_id)
        nodes.append({
            "data": {
                "id": fn_id,
                "label": fn.name,
                "file": fn.file,
                "zone": zone,
                "locked": graph.is_locked(fn_id),
                "callers": len(graph.callers(fn_id)),
                "kind": fn.kind,
                "complexity": fn.complexity,
                "line_start": fn.line_start,
                "line_end": fn.line_end,
                "params": [p.name for p in fn.params],
                "return_type": fn.return_type,
                "is_async": fn.is_async,
                "docstring": fn.docstring,
            }
        })

    for i, edge in enumerate(graph.call_edges):
        if zone_filter and (
            edge.source_fn not in fn_ids_in_zone
            or edge.target_fn not in fn_ids_in_zone
        ):
            continue
        edges.append({
            "data": {
                "id": f"e{i}",
                "source": edge.source_fn,
                "target": edge.target_fn,
                "confidence": edge.confidence,
                "expression": edge.call_site.expression,
                "line": edge.call_site.line,
            }
        })

    return {"nodes": nodes, "edges": edges}


def _preview(graph, fn_id: str) -> dict[str, Any]:
    """Return highlight sets for preview mode."""
    if fn_id not in graph.functions:
        # Try name lookup
        matches = [fid for fid, fn in graph.functions.items() if fn.name == fn_id]
        if not matches:
            return {"error": f"Function not found: {fn_id}"}
        fn_id = matches[0]

    caller_edges = graph.callers(fn_id)
    callee_edges = graph.callees(fn_id)

    highlight_locked = [
        e.source_fn for e in caller_edges if graph.is_locked(e.source_fn)
    ]
    highlight_neighbors = (
        [e.source_fn for e in caller_edges]
        + [e.target_fn for e in callee_edges]
    )

    return {
        "target": fn_id,
        "highlight_target": [fn_id],
        "highlight_locked": highlight_locked,
        "highlight_neighbors": list(set(highlight_neighbors)),
    }


def _history(root: Path) -> list[dict]:
    """Return list of snapshots from .winkers/history/."""
    history_dir = root / ".winkers" / "history"
    if not history_dir.exists():
        return []
    snapshots = []
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fns = len(data.get("functions", {}))
            edges = len(data.get("call_edges", []))
            snapshots.append({
                "file": path.name,
                "timestamp": path.stem.replace("T", " ").replace("-", ":", 2),
                "functions": fns,
                "call_edges": edges,
            })
        except Exception:
            pass
    return snapshots


def create_app(root: Path) -> web.Application:
    store = GraphStore(root)
    static_dir = Path(__file__).parent / "static"

    # Shared state for WebSocket broadcast
    ws_clients: set[web.WebSocketResponse] = set()

    async def handle_index(request: web.Request) -> web.Response:
        index = static_dir / "index.html"
        return web.Response(
            text=index.read_text(encoding="utf-8"),
            content_type="text/html",
        )

    async def handle_graph(request: web.Request) -> web.Response:
        graph = store.load()
        if graph is None:
            return web.json_response({"error": "Graph not initialized"}, status=404)
        zone = request.rel_url.query.get("zone")
        return web.json_response(_graph_to_cytoscape(graph, zone))

    async def handle_preview(request: web.Request) -> web.Response:
        graph = store.load()
        if graph is None:
            return web.json_response({"error": "Graph not initialized"}, status=404)
        fn = request.rel_url.query.get("fn", "")
        return web.json_response(_preview(graph, fn))

    async def handle_semantic(request: web.Request) -> web.Response:
        from winkers.semantic import SemanticStore
        sem = SemanticStore(root).load()
        if sem is None:
            return web.json_response({})
        return web.json_response(sem.model_dump())

    async def handle_history(request: web.Request) -> web.Response:
        return web.json_response(_history(root))

    async def handle_debt(request: web.Request) -> web.Response:
        debt_path = root / ".winkers" / "debt.json"
        if not debt_path.exists():
            return web.json_response({"error": "No debt data. Run winkers init."}, status=404)
        data = json.loads(debt_path.read_text(encoding="utf-8"))
        return web.json_response(data)

    async def handle_source(request: web.Request) -> web.Response:
        graph = store.load()
        if graph is None:
            return web.json_response({"error": "Graph not initialized"}, status=404)
        fn_id = request.rel_url.query.get("fn", "")
        fn = graph.functions.get(fn_id)
        if fn is None:
            # Try name lookup
            matches = [f for f in graph.functions.values() if f.name == fn_id]
            fn = matches[0] if matches else None
        if fn is None:
            return web.json_response({"error": f"Function not found: {fn_id}"})
        try:
            lines = (root / fn.file).read_text(encoding="utf-8").splitlines()
            source = "\n".join(lines[fn.line_start - 1:fn.line_end])
        except Exception as e:
            source = f"<could not read: {e}>"
        return web.json_response({
            "function": fn.id,
            "file": fn.file,
            "lines": f"{fn.line_start}-{fn.line_end}",
            "source": source,
        })

    async def handle_ws(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws_clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            ws_clients.discard(ws)
        return ws

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/graph", handle_graph)
    app.router.add_get("/api/preview", handle_preview)
    app.router.add_get("/api/semantic", handle_semantic)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/debt", handle_debt)
    app.router.add_get("/api/source", handle_source)
    app.router.add_get("/ws", handle_ws)
    return app


def run(root: Path, host: str = "127.0.0.1", port: int = 7420) -> None:
    """Start the dashboard HTTP server."""
    app = create_app(root)
    web.run_app(app, host=host, port=port, print=None)
