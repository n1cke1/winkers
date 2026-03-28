"""Dashboard HTTP + WebSocket server for Winkers."""

from __future__ import annotations

from pathlib import Path

from aiohttp import WSMsgType, web

from winkers.dashboard.handlers import constraints, data, graph, rules, sessions
from winkers.store import GraphStore


def create_app(root: Path) -> web.Application:
    store = GraphStore(root)
    static_dir = Path(__file__).parent / "static"

    # Shared state for WebSocket broadcast
    ws_clients: set[web.WebSocketResponse] = set()

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

    # Build handler namespaces
    h_graph = graph.make_handlers(root, store, static_dir)
    h_data = data.make_handlers(root, store)
    h_sessions = sessions.make_handlers(root, store)
    h_rules = rules.make_handlers(root)
    h_constraints = constraints.make_handlers(root)

    app = web.Application()
    app.router.add_get("/", h_graph.index)
    app.router.add_get("/api/graph", h_graph.graph)
    app.router.add_get("/api/preview", h_graph.preview)
    app.router.add_get("/api/semantic", h_data.semantic)
    app.router.add_get("/api/history", h_data.history)
    app.router.add_get("/api/snapshot-graph", h_graph.snapshot_graph)
    app.router.add_get("/api/debt", h_data.debt)
    app.router.add_get("/api/source", h_data.source)
    app.router.add_get("/api/sessions", h_sessions.sessions)
    app.router.add_get("/api/insights", h_sessions.insights)
    app.router.add_get("/api/tool-stats", h_sessions.tool_stats)
    app.router.add_get("/api/rules", h_rules.list)
    app.router.add_delete("/api/rules/{id}", h_rules.dismiss)
    app.router.add_post("/api/rules", h_rules.add)
    app.router.add_get("/api/constraints", h_constraints.get)
    app.router.add_post("/api/constraints", h_constraints.add)
    app.router.add_delete("/api/constraints/{idx}", h_constraints.delete)
    app.router.add_get("/ws", handle_ws)
    return app


def run(root: Path, host: str = "127.0.0.1", port: int = 7420) -> None:
    """Start the dashboard HTTP server."""
    app = create_app(root)
    web.run_app(app, host=host, port=port, print=None)
