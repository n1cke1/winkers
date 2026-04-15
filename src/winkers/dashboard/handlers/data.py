"""Data API handlers: semantic, history, debt, source."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from winkers.store import GraphStore


def make_handlers(root: Path, store: GraphStore) -> SimpleNamespace:
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

    async def handle_impact(request: web.Request) -> web.Response:
        """Per-function risk map for the dashboard heatmap layer."""
        from winkers.impact import ImpactStore
        impact = ImpactStore(root).load()
        if not impact.functions:
            return web.json_response({"functions": {}, "meta": impact.meta.model_dump()})
        compact = {
            fid: {
                "risk_level": r.risk_level,
                "risk_score": r.risk_score,
                "summary": r.summary,
            }
            for fid, r in impact.functions.items()
        }
        return web.json_response({
            "functions": compact,
            "meta": impact.meta.model_dump(),
        })

    async def handle_source(request: web.Request) -> web.Response:
        graph = store.load()
        if graph is None:
            return web.json_response({"error": "Graph not initialized"}, status=404)
        fn_id = request.rel_url.query.get("fn", "")
        fn = graph.functions.get(fn_id)
        if fn is None:
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

    return SimpleNamespace(
        semantic=handle_semantic,
        history=handle_history,
        debt=handle_debt,
        impact=handle_impact,
        source=handle_source,
    )


def _history(root: Path) -> list[dict]:
    """Return snapshots with diffs and git commits between them."""
    history_dir = root / ".winkers" / "history"
    if not history_dir.exists():
        return []

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

        # Compute debt score/density from snapshot graph
        debt_score: int | None = None
        debt_density: float | None = None
        try:
            from winkers.debt import compute_debt
            from winkers.models import Graph
            snap_graph = Graph.model_validate(data)
            report = compute_debt(snap_graph)
            debt_score = report.summary["score"]
            debt_density = report.summary["density"]
        except Exception:
            pass

        entry: dict[str, Any] = {
            "file": path.name,
            "timestamp": path.stem.replace("T", " ").replace("-", ":", 2),
            "functions": fn_count,
            "call_edges": edge_count,
            "files": file_count,
            "complexity": total_complexity,
            "debt_score": debt_score,
            "debt_density": debt_density,
        }

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

    _enrich_with_git_commits(root, snapshots)
    snapshots.reverse()
    return snapshots


def _enrich_with_git_commits(root: Path, snapshots: list[dict]) -> None:
    """Add git commits between consecutive snapshots."""
    from winkers.git import run_git

    stdout = run_git(
        ["log", "-50", "--pretty=format:%H|%ad|%s", "--date=iso-strict"],
        cwd=root,
    )
    if not stdout:
        return

    commits = []
    for line in stdout.splitlines():
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
