"""Sessions, insights, and tool-stats API handlers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from winkers.store import GraphStore


def make_handlers(root: Path, store: GraphStore) -> SimpleNamespace:
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

        items = InsightsStore(root).open_insights()
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

        estimates = _estimate_mcp_tokens(store.load(), root)
        recorded_names = {r["name"] for r in result}
        for est in estimates:
            if est["name"] not in recorded_names:
                result.append(est)
            else:
                for r in result:
                    if r["name"] == est["name"]:
                        r["estimated_out"] = est["estimated_out"]
                        break

        return web.json_response(result)

    return SimpleNamespace(
        sessions=handle_sessions,
        insights=handle_insights,
        tool_stats=handle_tool_stats,
    )


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
