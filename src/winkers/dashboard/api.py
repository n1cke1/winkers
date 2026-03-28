"""Dashboard HTTP + WebSocket server for Winkers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from winkers.store import GraphStore


def _graph_to_cytoscape(
    graph,
    zone_filter: str | None = None,
    include_ui: bool = False,
) -> dict[str, Any]:
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

    if include_ui:
        _add_ui_nodes(graph, fn_ids_in_zone, nodes, edges)

    return {"nodes": nodes, "edges": edges}


def _add_ui_nodes(graph, fn_ids_in_zone: set[str], nodes: list, edges: list) -> None:
    """Append UI layer nodes (templates + elements) and edges to Cytoscape lists."""
    ui_map: dict = graph.meta.get("ui_map", {})
    if not ui_map:
        return

    seen_templates: set[str] = set()
    ui_edge_idx = 0

    for route, info in ui_map.items():
        fn_id = next(
            (fid for fid, fn in graph.functions.items()
             if fn.route == route and fid in fn_ids_in_zone),
            None,
        )
        # If zone filter active and route handler not in zone, skip
        if fn_ids_in_zone and fn_id is None:
            continue

        tpl_name = info.get("template", "")
        tpl_id = f"ui::tpl::{tpl_name}"

        if tpl_name not in seen_templates:
            seen_templates.add(tpl_name)
            nodes.append({"data": {
                "id": tpl_id,
                "label": tpl_name.split("/")[-1],
                "layer": "ui",
                "kind": "template",
                "template": tpl_name,
                "callers": 0,
                "locked": False,
            }})

        if fn_id:
            edges.append({"data": {
                "id": f"ui-r-{ui_edge_idx}",
                "source": fn_id,
                "target": tpl_id,
                "edge_kind": "renders",
            }})
            ui_edge_idx += 1

        for el_idx, el in enumerate(info.get("elements", [])):
            el_label = el.get("id") or el.get("data-tab") or el.get("text") or el["kind"]
            el_id = f"ui::el::{tpl_name}::{el['kind']}::{el_idx}"
            nodes.append({"data": {
                "id": el_id,
                "label": el_label[:20],
                "layer": "ui",
                "kind": el["kind"],
                "template": tpl_name,
                "callers": 0,
                "locked": False,
            }})
            edges.append({"data": {
                "id": f"ui-c-{ui_edge_idx}",
                "source": tpl_id,
                "target": el_id,
                "edge_kind": "contains",
            }})
            ui_edge_idx += 1


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
    """Return snapshots with diffs and git commits between them."""
    history_dir = root / ".winkers" / "history"
    if not history_dir.exists():
        return []

    # Load all snapshots chronologically
    paths = sorted(history_dir.glob("*.json"))
    snapshots: list[dict] = []
    prev_data: dict | None = None

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        fns = data.get("functions", {})
        edges = data.get("call_edges", [])
        files = data.get("files", {})
        fn_count = len(fns)
        edge_count = len(edges)
        file_count = len(files)
        total_complexity = sum(
            f.get("complexity", 0) for f in fns.values()
        )

        entry: dict[str, Any] = {
            "file": path.name,
            "timestamp": path.stem.replace("T", " ").replace("-", ":", 2),
            "functions": fn_count,
            "call_edges": edge_count,
            "files": file_count,
            "complexity": total_complexity,
        }

        # Diff with previous snapshot
        if prev_data is not None:
            prev_fns = prev_data.get("functions", {})
            prev_edges = prev_data.get("call_edges", [])
            prev_files = prev_data.get("files", {})
            prev_cx = sum(
                f.get("complexity", 0) for f in prev_fns.values()
            )

            added_fns = [f for f in fns if f not in prev_fns]
            removed_fns = [f for f in prev_fns if f not in fns]
            added_files = [f for f in files if f not in prev_files]
            removed_files = [f for f in prev_files if f not in files]

            ui_map = data.get("meta", {}).get("ui_map", {})
            prev_ui_map = prev_data.get("meta", {}).get("ui_map", {})
            added_ui = [r for r in ui_map if r not in prev_ui_map]
            removed_ui = [r for r in prev_ui_map if r not in ui_map]
            ui_element_delta = sum(
                len(v.get("elements", [])) for v in ui_map.values()
            ) - sum(
                len(v.get("elements", [])) for v in prev_ui_map.values()
            )

            entry["diff"] = {
                "functions_delta": fn_count - len(prev_fns),
                "edges_delta": edge_count - len(prev_edges),
                "files_delta": file_count - len(prev_files),
                "complexity_delta": total_complexity - prev_cx,
                "added_functions": added_fns[:10],
                "removed_functions": removed_fns[:10],
                "added_files": added_files,
                "removed_files": removed_files,
                "added_ui_routes": added_ui,
                "removed_ui_routes": removed_ui,
                "ui_elements_delta": ui_element_delta,
            }

        prev_data = data
        snapshots.append(entry)

    # Add git commits between snapshots
    _enrich_with_git_commits(root, snapshots)

    # Return newest first
    snapshots.reverse()
    return snapshots


def _enrich_with_git_commits(root: Path, snapshots: list[dict]) -> None:
    """Add git commits between consecutive snapshots."""
    import subprocess
    import sys

    try:
        kwargs: dict[str, Any] = {
            "capture_output": True, "text": True,
            "cwd": str(root), "timeout": 10,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["git", "log", "-50", "--pretty=format:%H|%ad|%s",
             "--date=iso-strict"],
            **kwargs,
        )
    except Exception:
        return

    if result.returncode != 0:
        return

    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "sha": parts[0][:8],
                "date": parts[1],
                "message": parts[2],
            })

    for i, snap in enumerate(snapshots):
        ts = snap["timestamp"].replace(" ", "T").replace(":", "-", 2)
        next_ts = snapshots[i + 1]["timestamp"].replace(
            " ", "T"
        ).replace(":", "-", 2) if i + 1 < len(snapshots) else "9999"

        snap["commits"] = [
            c for c in commits
            if ts <= c["date"].replace(":", "-", 2) < next_ts
        ][:10]


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
        include_ui = request.rel_url.query.get("ui") == "1"
        return web.json_response(_graph_to_cytoscape(graph, zone, include_ui))

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

    async def handle_sessions(request: web.Request) -> web.Response:
        from winkers.scoring import score_label
        from winkers.session_store import SessionStore

        sessions = SessionStore(root).load_all()
        result = []
        for s in sessions:
            result.append({
                "session_id": s.session.session_id,
                "task_prompt": s.session.task_prompt,
                "started_at": s.session.started_at,
                "model": s.session.model,
                "total_turns": s.session.total_turns,
                "exploration_turns": s.session.exploration_turns,
                "modification_turns": s.session.modification_turns,
                "verification_turns": s.session.verification_turns,
                "files_modified": s.session.files_modified,
                "files_created": s.session.files_created,
                "tests_passed": s.session.tests_passed,
                "session_end": s.session.session_end,
                "winkers_calls": s.session.winkers_calls,
                "tool_calls": [
                    {
                        "name": tc.name,
                        "input_params": tc.input_params,
                        "is_error": tc.is_error,
                    }
                    for tc in s.session.tool_calls
                ],
                "score": s.score,
                "score_label": score_label(s.score),
                "commit": s.commit.model_dump(),
                "debt": s.debt.model_dump(),
            })
        return web.json_response(result)

    async def handle_insights(request: web.Request) -> web.Response:
        from winkers.insights_store import InsightsStore

        store = InsightsStore(root)
        items = store.open_insights()
        result = []
        for item in items:
            result.append({
                "category": item.category,
                "description": item.description,
                "turns_wasted": item.turns_wasted,
                "tokens_wasted": item.tokens_wasted,
                "semantic_target": item.semantic_target,
                "injection_content": item.injection_content,
                "priority": item.priority,
                "occurrences": item.occurrences,
                "session_ids": item.session_ids,
            })
        return web.json_response(result)

    async def handle_tool_stats(request: web.Request) -> web.Response:
        from winkers.session_store import SessionStore

        sessions = SessionStore(root).load_all()
        stats: dict[str, dict] = {}
        for s in sessions:
            for tc in s.session.tool_calls:
                name = tc.name
                if name not in stats:
                    stats[name] = {
                        "calls": 0,
                        "tokens_in": 0,
                        "tokens_out": 0,
                    }
                stats[name]["calls"] += 1
                stats[name]["tokens_in"] += tc.tokens_in
                stats[name]["tokens_out"] += tc.tokens_out

        result = []
        for name, st in sorted(
            stats.items(),
            key=lambda x: x[1]["tokens_in"] + x[1]["tokens_out"],
            reverse=True,
        ):
            total = st["tokens_in"] + st["tokens_out"]
            avg = total // st["calls"] if st["calls"] else 0
            result.append({
                "name": name,
                "calls": st["calls"],
                "tokens_in": st["tokens_in"],
                "tokens_out": st["tokens_out"],
                "tokens_total": total,
                "tokens_avg": avg,
                "source": "recorded",
            })

        # Add estimated stats for Winkers MCP tools from graph
        estimates = _estimate_mcp_tokens(store.load(), root)
        recorded_names = {r["name"] for r in result}
        for est in estimates:
            if est["name"] not in recorded_names:
                result.append(est)
            else:
                # Enrich recorded entry with estimate
                for r in result:
                    if r["name"] == est["name"]:
                        r["estimated_out"] = est["estimated_out"]
                        break

        return web.json_response(result)

    async def handle_rules(request: web.Request) -> web.Response:
        from winkers.conventions import RulesStore
        rules_file = RulesStore(root).load()
        return web.json_response([
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "content": r.content,
                "wrong_approach": r.wrong_approach,
                "source": r.source,
                "created": r.created,
                "stats": r.stats.model_dump(),
            }
            for r in rules_file.rules
        ])

    async def handle_rules_dismiss(request: web.Request) -> web.Response:
        from winkers.conventions import DismissedStore, RuleAdd, RulesStore
        try:
            rule_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "invalid id"}, status=400)
        rules_store = RulesStore(root)
        rules_file = rules_store.load()
        rule = next((r for r in rules_file.rules if r.id == rule_id), None)
        if rule is None:
            return web.json_response({"error": "not found"}, status=404)
        DismissedStore(root).merge(
            [RuleAdd(category=rule.category, title=rule.title, content=rule.content)],
            [], []
        )
        rules_file.rules = [r for r in rules_file.rules if r.id != rule_id]
        rules_store.save(rules_file)
        return web.json_response({"ok": True})

    async def handle_rules_add(request: web.Request) -> web.Response:
        from datetime import date

        from winkers.conventions import ConventionRule, RulesStore
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        category = data.get("category", "").strip()
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        if not category or not title or not content:
            return web.json_response({"error": "category, title, content required"}, status=400)
        rules_store = RulesStore(root)
        rules_file = rules_store.load()
        rule = ConventionRule(
            id=rules_store.next_id(rules_file),
            category=category,
            title=title,
            content=content,
            wrong_approach=data.get("wrong_approach", ""),
            source="manual",
            created=date.today().isoformat(),
        )
        rules_file.rules.append(rule)
        rules_store.save(rules_file)
        return web.json_response(rule.model_dump())

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
        store = SemanticStore(root)
        layer = store.load()
        if layer is None:
            return web.json_response({"error": "No semantic.json. Run winkers init."}, status=404)
        layer.constraints.append(text)
        store.save(layer)
        return web.json_response({"ok": True, "constraints": layer.constraints})

    async def handle_constraints_delete(request: web.Request) -> web.Response:
        from winkers.semantic import SemanticStore
        try:
            idx = int(request.match_info["idx"])
        except (KeyError, ValueError):
            return web.json_response({"error": "invalid index"}, status=400)
        store = SemanticStore(root)
        layer = store.load()
        if layer is None or idx < 0 or idx >= len(layer.constraints):
            return web.json_response({"error": "not found"}, status=404)
        layer.constraints.pop(idx)
        store.save(layer)
        return web.json_response({"ok": True, "constraints": layer.constraints})

    async def handle_snapshot_graph(request: web.Request) -> web.Response:
        """Load a historical snapshot as Cytoscape graph."""
        filename = request.rel_url.query.get("file", "")
        if not filename:
            return web.json_response({"error": "file parameter required"}, status=400)

        history_dir = root / ".winkers" / "history"
        snap_path = history_dir / filename
        if not snap_path.exists() or ".." in filename:
            return web.json_response({"error": "Snapshot not found"}, status=404)

        try:
            from winkers.models import Graph
            data = json.loads(snap_path.read_text(encoding="utf-8"))
            snap_graph = Graph.model_validate(data)
            zone = request.rel_url.query.get("zone")
            include_ui = request.rel_url.query.get("ui") == "1"
            return web.json_response(_graph_to_cytoscape(snap_graph, zone, include_ui))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/graph", handle_graph)
    app.router.add_get("/api/preview", handle_preview)
    app.router.add_get("/api/semantic", handle_semantic)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/snapshot-graph", handle_snapshot_graph)
    app.router.add_get("/api/debt", handle_debt)
    app.router.add_get("/api/source", handle_source)
    app.router.add_get("/api/sessions", handle_sessions)
    app.router.add_get("/api/insights", handle_insights)
    app.router.add_get("/api/tool-stats", handle_tool_stats)
    app.router.add_get("/api/rules", handle_rules)
    app.router.add_delete("/api/rules/{id}", handle_rules_dismiss)
    app.router.add_post("/api/rules", handle_rules_add)
    app.router.add_get("/api/constraints", handle_constraints_get)
    app.router.add_post("/api/constraints", handle_constraints_add)
    app.router.add_delete("/api/constraints/{idx}", handle_constraints_delete)
    app.router.add_get("/ws", handle_ws)
    return app


def _estimate_mcp_tokens(graph, root: Path) -> list[dict]:
    """Estimate output tokens for each Winkers MCP tool from the current graph."""
    if graph is None:
        return []

    from winkers.mcp.tools import (
        _section_functions_graph,
        _section_hotspots,
        _section_map,
        _tool_scope,
    )

    estimates = []

    def _chars_to_tokens(text: str) -> int:
        return len(text) // 4  # ~4 chars per token

    # orient(include=["map"])
    try:
        result = _section_map(graph, None, root)
        tokens = _chars_to_tokens(json.dumps(result))
        estimates.append({
            "name": "mcp__winkers__orient",
            "calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "tokens_avg": 0,
            "estimated_out": tokens,
            "source": "estimated",
        })
    except Exception:
        pass

    # orient(include=["functions_graph"])
    try:
        result = _section_functions_graph(graph, None)
        tokens = _chars_to_tokens(json.dumps(result))
        estimates.append({
            "name": "mcp__winkers__orient_functions_graph",
            "calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "tokens_avg": 0,
            "estimated_out": tokens,
            "source": "estimated",
        })
    except Exception:
        pass

    # orient(include=["hotspots"])
    try:
        result = _section_hotspots(graph, 10)
        tokens = _chars_to_tokens(json.dumps(result))
        estimates.append({
            "name": "mcp__winkers__orient_hotspots",
            "calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "tokens_avg": 0,
            "estimated_out": tokens,
            "source": "estimated",
        })
    except Exception:
        pass

    # scope() — estimate for average function
    try:
        fn_ids = list(graph.functions.keys())
        if fn_ids:
            by_callers = sorted(fn_ids, key=lambda f: len(graph.callers(f)))
            mid = by_callers[len(by_callers) // 2]
            result = _tool_scope(graph, {"function": mid}, root)
            tokens = _chars_to_tokens(json.dumps(result))
            estimates.append({
                "name": "mcp__winkers__scope",
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_total": 0,
                "tokens_avg": 0,
                "estimated_out": tokens,
                "source": "estimated",
            })
    except Exception:
        pass

    return estimates


def run(root: Path, host: str = "127.0.0.1", port: int = 7420) -> None:
    """Start the dashboard HTTP server."""
    app = create_app(root)
    web.run_app(app, host=host, port=port, print=None)
