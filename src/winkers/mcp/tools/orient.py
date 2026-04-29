"""MCP tool: orient — single entry point for project context (map / conventions / rules / etc)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from winkers.mcp.tools._common import (
    MAX_ORIENT_TOKENS,
    _attach_route,
    _coerce_include,
    _estimate_tokens,
    _get_hotspots,
    _load_impact,
    _load_rules,
    _load_semantic,
    _one_liner,
    _signature,
    _try_compact,
    _zone_imported_by,
    _zone_imports_from,
)
from winkers.mcp.tools.find_work_area import _tool_find_work_area
from winkers.models import Graph

# Priority order: most important sections first for truncation.
_SECTION_PRIORITY = [
    "map", "conventions", "rules_list", "hotspots",
    "routes", "ui_map", "functions_graph",
]

# Sections that have a compact fallback + the minimum tokens to reserve
# for each when it's been explicitly requested. Prevents earlier sections
# from starving the compact-capable ones; observed on large projects where
# map + conventions ate the entire budget and rules_list silently vanished.
_COMPACT_RESERVE: dict[str, int] = {
    "rules_list": 200,
}


def _tool_orient(graph: Graph, args: dict, root: Path) -> dict:
    task = (args.get("task") or "").strip()
    if not task:
        return {
            "error": (
                "task required: provide a one-sentence task description"
                " (verb + scope, e.g. 'simplify invoice statuses from 6 to 3')."
            )
        }

    include = _coerce_include(args.get("include", []))
    zone = args.get("zone")
    min_callers = args.get("min_callers", 10)
    max_tokens = args.get("max_tokens", MAX_ORIENT_TOKENS)
    k = int(args.get("k", 5)) if args.get("k") else 5

    builders: dict[str, Any] = {
        "map": lambda: _section_map(graph, zone, root),
        "functions_graph": lambda: _section_functions_graph(graph, zone),
        "conventions": lambda: _section_conventions(root),
        "rules_list": lambda: _section_rules_list(root),
        "hotspots": lambda: _section_hotspots(graph, min_callers, root),
        "routes": lambda: _section_routes(graph, zone),
        "ui_map": lambda: _section_ui_map(graph, zone),
    }

    # Process sections in priority order (only those requested).
    ordered = [s for s in _SECTION_PRIORITY if s in include]

    result: dict[str, Any] = {}
    used_tokens = 0
    skipped: list[str] = []
    compacted: list[str] = []

    # Reserve a floor for compact-capable sections that are still pending,
    # so earlier sections don't consume the entire budget.
    pending_compact = [s for s in ordered if s in _COMPACT_RESERVE]

    for section in ordered:
        build = builders.get(section)
        if build is None:
            continue

        # Effective ceiling for THIS section: full budget minus whatever we
        # still need to reserve for yet-to-process compact-capable sections.
        if section in pending_compact:
            pending_compact.remove(section)
        reserved = sum(_COMPACT_RESERVE[s] for s in pending_compact)
        effective_max = max_tokens - reserved

        data = build()
        section_tokens = _estimate_tokens(data)

        if used_tokens + section_tokens > effective_max and result:
            # Try a compact variant before giving up on the section entirely.
            compact = _try_compact(section, data)
            if compact is not None:
                compact_tokens = _estimate_tokens(compact)
                # Compact gets the FULL budget (not effective_max) — this is
                # the fallback path, no further reservation needed.
                if used_tokens + compact_tokens <= max_tokens:
                    result[section] = compact
                    used_tokens += compact_tokens
                    compacted.append(section)
                    continue
            skipped.append(section)
            continue
        result[section] = data
        used_tokens += section_tokens

    if skipped or compacted:
        result["_truncated"] = True
        hint_parts = [f"Response constrained to ~{max_tokens} token budget."]
        if skipped:
            hint_parts.append(f"Skipped: {', '.join(skipped)}.")
        if compacted:
            hint_parts.append(
                f"Compacted (titles only): {', '.join(compacted)} — "
                "use rule_read(category) for wrong_approach."
            )
        hint_parts.append("Call orient() with fewer includes or filter by zone.")
        result["_hint"] = " ".join(hint_parts)

    if not result:
        result["error"] = (
            "No valid include values. Use: map, conventions, rules_list,"
            " functions_graph, hotspots, routes, ui_map"
        )
        return result

    # Show session status if active
    session_info = _session_status(root)
    if session_info:
        result["session"] = session_info

    # Always compute semantic_matches against the registered task — this is
    # the merged orient + find_work_area pathway (Wave 2 of the redesign).
    fwa = _tool_find_work_area(graph, {"query": task, "k": k}, root)
    if "matches" in fwa:
        result["semantic_matches"] = fwa.get("matches", [])
        result["semantic_verdict"] = fwa.get("verdict", "EMPTY")
        result["semantic_top_score"] = fwa.get("max_score", 0.0)
    else:
        # Index missing / warming / empty — surface a hint but don't fail
        # the whole orient call (the session-context sections are still
        # useful even without semantic ranking).
        result["semantic_matches"] = []
        hint = fwa.get("hint") or fwa.get("error") or "semantic index unavailable"
        result["semantic_hint"] = hint

    task_warnings = _validate_task(task, result.get("semantic_matches", []))
    if task_warnings:
        result["task_warnings"] = task_warnings

    return result


# Soft-validation thresholds for the orient(task) parameter.
# These are diagnostics, not gates — the agent always gets results; warnings
# are surface-level guidance per the "Intent formation rules" concept (Wave 2).
_TASK_MIN_WORDS = 3
_TASK_MULTI_RE = re.compile(r"\band\b|&", re.IGNORECASE)
_TASK_VERB_RE = re.compile(
    r"\b(create|change|fix|add|refactor|extract|remove|rename|audit"
    r"|simplify|implement|update|build|delete|move)\b",
    re.IGNORECASE,
)
_TASK_MIN_GOOD_SCORE = 0.5


def _validate_task(task: str, matches: list[dict]) -> list[str]:
    """Return human-readable warnings when the task is structurally weak.

    Three checks today (CONCEPT.md §6 "Intent formation rules"):
      * too short  → likely missing verb or scope
      * multi-task → 'and'/'&' plus ≥2 verbs dilute the audit signal
      * no matches → no semantic_matches scored above threshold
    """
    warnings: list[str] = []
    words = [w for w in task.split() if w]

    if len(words) < _TASK_MIN_WORDS:
        warnings.append(
            "task is very short — recommend adding a verb + scope"
            " (e.g. 'fix Class.method', 'audit X consistency')."
        )

    if _TASK_MULTI_RE.search(task):
        verbs = _TASK_VERB_RE.findall(task)
        if len(verbs) >= 2:
            warnings.append(
                "task looks multi-task (has 'and/&' plus multiple verbs)"
                " — split into separate orient/before_create cycles for"
                " cleaner audit and risk gates."
            )

    if matches:
        top_score = max((m.get("score", 0.0) for m in matches), default=0.0)
        if top_score < _TASK_MIN_GOOD_SCORE:
            warnings.append(
                f"task didn't match any indexed area well"
                f" (top score {top_score:.2f} < {_TASK_MIN_GOOD_SCORE})"
                " — rephrase with a named target or domain term."
            )

    return warnings


def _section_map(graph: Graph, zone_filter: str | None, root: Path) -> dict:
    from winkers.protect import load_startup_chain
    startup_chain = load_startup_chain(root)

    zones: dict[str, list[str]] = {}
    for f in graph.files.values():
        z = f.zone or "unknown"
        zones.setdefault(z, []).append(f.path)

    if zone_filter:
        zones = {z: files for z, files in zones.items() if z == zone_filter}

    semantic = _load_semantic(root)

    zone_list = []
    for z, files in sorted(zones.items()):
        entry: dict[str, Any] = {
            "name": z,
            "files": len(files),
            "functions": sum(
                len(graph.files[f].function_ids) for f in files if f in graph.files
            ),
            "imports_from": _zone_imports_from(z, zones, graph),
            "imported_by": _zone_imported_by(z, zones, graph),
        }
        route_count = sum(
            1 for f in files
            for fn_id in (graph.files[f].function_ids if f in graph.files else [])
            if graph.functions.get(fn_id) and graph.functions[fn_id].route
        )
        if route_count:
            entry["routes_count"] = route_count
        protected_count = sum(1 for f in files if f in startup_chain)
        if protected_count:
            entry["startup_chain"] = protected_count
        if semantic and z in semantic.zone_intents:
            intent = semantic.zone_intents[z]
            entry["intent"] = {"why": intent.why, "wrong_approach": intent.wrong_approach}
        zone_list.append(entry)

    result: dict[str, Any] = {
        "total_files": len(graph.files),
        "total_functions": len(graph.functions),
        "languages": graph.meta.get("languages", []),
        "zones": zone_list,
        "hotspots_top5": _get_hotspots(graph, top=5),
    }
    if semantic and semantic.data_flow:
        result["data_flow"] = semantic.data_flow
    if semantic and semantic.data_flow_targets:
        result["data_flow_targets"] = semantic.data_flow_targets
    return result


def _section_functions_graph(graph: Graph, zone_filter: str | None) -> dict:
    fn_ids = sorted(graph.functions.keys())
    if zone_filter:
        fn_ids = [fid for fid in fn_ids
                  if graph.file_zone(graph.functions[fid].file) == zone_filter]

    id_to_idx: dict[str, int] = {fid: i + 1 for i, fid in enumerate(fn_ids)}
    caller_map: dict[str, list[str]] = {}
    for edge in graph.call_edges:
        caller_map.setdefault(edge.target_fn, []).append(edge.source_fn)

    functions: dict[str, dict] = {}
    for fid in fn_ids:
        fn = graph.functions[fid]
        idx = id_to_idx[fid]
        caller_indices = sorted(
            id_to_idx[c] for c in caller_map.get(fid, []) if c in id_to_idx
        )
        entry: dict[str, Any] = {"id": fn.id, "name": fn.name, "file": fn.file}
        if caller_indices:
            entry["callers"] = caller_indices
        if fn.complexity and fn.complexity > 1:
            entry["cx"] = fn.complexity
        functions[str(idx)] = entry

    return {"total": len(functions), "functions": functions}


def _section_conventions(root: Path) -> dict:
    semantic = _load_semantic(root)
    if semantic is None:
        return {"note": "No semantic.json found. Run winkers init."}

    result: dict[str, Any] = {}
    if semantic.domain_context:
        result["domain_context"] = semantic.domain_context
    if semantic.zone_intents:
        result["zone_intents"] = {
            z: {"why": i.why, "wrong_approach": i.wrong_approach}
            for z, i in semantic.zone_intents.items()
        }
    if semantic.monster_files:
        result["monster_files"] = {
            f: {"sections": [s.model_dump() for s in m.sections],
                "where_to_add": m.where_to_add}
            for f, m in semantic.monster_files.items()
        }
    if semantic.constraints:
        result["project_constraints"] = semantic.constraints
    if semantic.new_feature_checklist:
        result["before_writing_code"] = semantic.new_feature_checklist
    return result


def _section_rules_list(root: Path) -> dict:
    rules_file = _load_rules(root)
    if not rules_file.rules:
        return {"note": "No rules yet. Run winkers init or winkers conventions add."}

    by_category: dict[str, list[dict]] = {}
    for r in rules_file.rules:
        entry: dict = {
            "id": r.id,
            "title": r.title,
        }
        snippet = _one_liner(r.wrong_approach)
        if snippet:
            entry["wrong_approach"] = snippet
        if r.related:
            entry["related"] = r.related
        by_category.setdefault(r.category, []).append(entry)

    return {
        "total": len(rules_file.rules),
        "categories": {
            cat: rules for cat, rules in sorted(by_category.items())
        },
    }


def _section_hotspots(graph: Graph, min_callers: int, root: Path | None = None) -> dict:
    hotspots = []
    impact = _load_impact(root) if root is not None else None
    for fn_id, fn in graph.functions.items():
        caller_edges = graph.callers(fn_id)
        if len(caller_edges) < min_callers:
            continue
        entry: dict = {
            "function": fn_id,
            "file": fn.file,
            "signature": _signature(fn),
            "callers_count": len(caller_edges),
            "callers": [
                {
                    "fn": e.source_fn,
                    "file": e.call_site.file,
                    "line": e.call_site.line,
                    "expression": e.call_site.expression,
                    "confidence": e.confidence,
                }
                for e in caller_edges
            ],
        }
        if fn.intent:
            entry["intent"] = fn.intent
        _attach_route(entry, fn)
        if impact is not None:
            report = impact.functions.get(fn_id)
            if report is not None:
                entry["risk_level"] = report.risk_level
                entry["risk_score"] = report.risk_score
        hotspots.append(entry)
    hotspots.sort(key=lambda h: h["callers_count"], reverse=True)
    return {"min_callers": min_callers, "count": len(hotspots), "hotspots": hotspots}


def _section_routes(graph: Graph, zone_filter: str | None) -> dict:
    routes = []
    for fn in graph.functions.values():
        if not fn.route:
            continue
        if zone_filter and graph.file_zone(fn.file) != zone_filter:
            continue
        callees = [e.target_fn.split("::")[-1] for e in graph.callees(fn.id)]
        entry: dict = {
            "method": fn.http_method or "GET",
            "path": fn.route,
            "handler": fn.name,
            "file": fn.file,
            "calls": callees[:8],
        }
        if fn.template:
            entry["template"] = fn.template
        routes.append(entry)
    routes.sort(key=lambda r: (r["file"], r["path"]))
    if not routes:
        return {"count": 0, "routes": [],
                "note": "No routes found. Project may not use decorators."}
    return {"count": len(routes), "routes": routes}


def _section_ui_map(graph: Graph, zone_filter: str | None) -> dict:
    raw: dict = graph.meta.get("ui_map", {})
    if not raw:
        return {"count": 0, "routes": {},
                "note": "No UI map. No templates found or project has no Flask routes."}
    if zone_filter:
        raw = {
            path: data for path, data in raw.items()
            if graph.file_zone(data.get("file", "")) == zone_filter
        }
    return {"count": len(raw), "routes": raw}


def _session_status(root: Path) -> dict | None:
    """Return brief session status if an active session exists."""
    from winkers.session.state import SessionStore

    session_store = SessionStore(root)
    session = session_store.load()
    if session is None:
        return None

    pending = session.pending_warnings()
    info: dict = {
        "writes": len(session.writes),
        "warnings": len(session.warnings),
        "warnings_pending": len(pending),
    }
    if pending:
        info["pending"] = [w.detail for w in pending[:3]]
    return info
