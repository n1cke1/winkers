"""Constraints API handlers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aiohttp import web


def make_handlers(root: Path) -> SimpleNamespace:
    async def handle_constraints_get(request: web.Request) -> web.Response:
        from winkers.semantic import SemanticStore
        layer = SemanticStore(root).load()
        return web.json_response(layer.constraints if layer else [])

    async def handle_constraints_add(request: web.Request) -> web.Response:
        from winkers.semantic import SemanticStore
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"error": "text required"}, status=400)
        sem_store = SemanticStore(root)
        layer = sem_store.load()
        if layer is None:
            return web.json_response({"error": "No semantic.json. Run winkers init."}, status=404)
        layer.constraints.append(text)
        sem_store.save(layer)
        return web.json_response({"ok": True, "constraints": layer.constraints})

    async def handle_constraints_delete(request: web.Request) -> web.Response:
        from winkers.semantic import SemanticStore
        try:
            idx = int(request.match_info["idx"])
        except (KeyError, ValueError):
            return web.json_response({"error": "invalid index"}, status=400)
        sem_store = SemanticStore(root)
        layer = sem_store.load()
        if layer is None or idx < 0 or idx >= len(layer.constraints):
            return web.json_response({"error": "not found"}, status=404)
        layer.constraints.pop(idx)
        sem_store.save(layer)
        return web.json_response({"ok": True, "constraints": layer.constraints})

    return SimpleNamespace(
        get=handle_constraints_get,
        add=handle_constraints_add,
        delete=handle_constraints_delete,
    )
