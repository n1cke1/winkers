"""Shared helpers for MCP tools — loaders, compaction, generic graph utilities.

Sub-modules under `winkers.mcp.tools.<tool>` import from here directly to
avoid pulling on the parent package mid-load. Externally importable
symbols (`MAX_ORIENT_TOKENS`, `_load_*`, `_try_compact`, `_validate_task`,
…) are re-exported by `winkers.mcp.tools.__init__` so existing call-sites
keep working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from winkers.models import Graph

MAX_ORIENT_TOKENS = 2500


# ---------------------------------------------------------------------------
# Loaders + logging
# ---------------------------------------------------------------------------


def _log_call(root: Path, tool: str, args: dict) -> None:
    """Append MCP tool call to .winkers/mcp.log."""
    import datetime
    try:
        log_path = root / ".winkers" / "mcp.log"
        log_path.parent.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        args_str = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {tool}({args_str})\n")
    except Exception:
        pass


def _load_semantic(root: Path):
    from winkers.semantic import SemanticStore
    return SemanticStore(root).load()


def _load_impact(root: Path):
    """Load impact.json once per tool call; returns None if missing/empty."""
    from winkers.impact import ImpactStore
    impact = ImpactStore(root).load()
    if not impact.functions:
        return None
    return impact


def _load_rules(root: Path):
    from winkers.conventions import RulesStore
    return RulesStore(root).load()


# ---------------------------------------------------------------------------
# Token budget compaction (used by orient)
# ---------------------------------------------------------------------------


def _estimate_tokens(data: Any) -> int:
    """Rough token count: ~4 chars per token in JSON output."""
    import json
    return len(json.dumps(data, default=str)) // 4


def _try_compact(section: str, data: Any) -> Any | None:
    """Return a smaller variant of `data` when budget is tight, or None.

    Right now we only know how to shrink `rules_list` (drop wrong_approach
    and related fields — agents can still see the categories and rule titles,
    and fetch details via rule_read). Other sections return None, which the
    caller treats as "skip the section entirely". Also returns None when the
    compact form would be empty (nothing useful to surface → prefer skip so
    agents see it in `_skipped` rather than getting a silent empty dict).
    """
    if section != "rules_list":
        return None
    if not isinstance(data, dict):
        return None
    categories = data.get("categories")
    if not isinstance(categories, dict) or not categories:
        return None
    compact_categories: dict[str, list[dict]] = {}
    for cat, rules in categories.items():
        compact_categories[cat] = [
            {k: v for k, v in r.items() if k in ("id", "title")}
            for r in rules
        ]
    if not compact_categories or not any(compact_categories.values()):
        return None
    return {
        "total": data.get("total"),
        "categories": compact_categories,
        "_compacted": "titles only — call rule_read(category) for wrong_approach",
    }


def _coerce_include(value: Any) -> list[str]:
    """Accept array, JSON-encoded array string, or single section name.

    Claude Sonnet sometimes serialises array arguments as a JSON-encoded
    string (`'["map","rules_list"]'`). Haiku sometimes sends a single
    section name string (`'map'`) when the caller means a one-element
    array. Normalise both to a plain list of names.
    """
    import json as _json

    if isinstance(value, list):
        return [str(s) for s in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(s) for s in parsed]
            except _json.JSONDecodeError:
                pass
        return [stripped] if stripped else []
    return []


# ---------------------------------------------------------------------------
# Text utility
# ---------------------------------------------------------------------------


def _one_liner(text: str, limit: int = 140) -> str:
    """Collapse text to a single-line snippet truncated to `limit` chars."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Generic graph helpers — shared by orient/scope/before_create/browse
# ---------------------------------------------------------------------------


def _find_function(name: str, graph: Graph):
    if name in graph.functions:
        return graph.functions[name]
    matches = [fn for fn in graph.functions.values() if fn.name == name]
    return matches[0] if len(matches) == 1 else None


def _signature(fn: Any) -> str:
    params = ", ".join(
        f"{p.name}: {p.type_hint}" if p.type_hint else p.name
        for p in fn.params
    )
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    return f"({params}){ret}"


def _zone_imports_from(zone: str, zones: dict[str, list[str]], graph: Graph) -> list[str]:
    zone_files = set(zones.get(zone, []))
    imported_zones: set[str] = set()
    for edge in graph.import_edges:
        if edge.source_file in zone_files:
            target_zone = graph.file_zone(edge.target_file)
            if target_zone != zone:
                imported_zones.add(target_zone)
    return sorted(imported_zones)


def _zone_imported_by(zone: str, zones: dict[str, list[str]], graph: Graph) -> list[str]:
    zone_files = set(zones.get(zone, []))
    importing_zones: set[str] = set()
    for edge in graph.import_edges:
        if edge.target_file in zone_files:
            source_zone = graph.file_zone(edge.source_file)
            if source_zone != zone:
                importing_zones.add(source_zone)
    return sorted(importing_zones)


def _get_hotspots(graph: Graph, top: int = 5) -> list[dict]:
    scored = [(fid, len(graph.callers(fid))) for fid in graph.functions]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "fn": fid,
            "file": graph.functions[fid].file,
            "call_count": count,
            "locked": graph.is_locked(fid),
        }
        for fid, count in scored[:top]
        if count > 0
    ]


def _recent_changes_from_graph(fn: Any, graph: Graph) -> list[dict]:
    file_node = graph.files.get(fn.file)
    if not file_node or not file_node.recent_commits:
        return []
    return file_node.recent_commits


def _semantic_context_for_fn(fn: Any, graph: Graph, root: Path | None) -> dict:
    """Return zone_intent and data_flow for the function's zone."""
    if root is None:
        return {}
    semantic = _load_semantic(root)
    if semantic is None:
        return {}
    zone = graph.file_zone(fn.file)
    ctx: dict[str, Any] = {}
    if zone in semantic.zone_intents:
        intent = semantic.zone_intents[zone]
        ctx["zone_intent"] = {"why": intent.why, "wrong_approach": intent.wrong_approach}
    if semantic.data_flow:
        ctx["data_flow"] = semantic.data_flow
    if semantic.data_flow_targets:
        ctx["data_flow_targets"] = semantic.data_flow_targets
    return ctx


def _related_rules(fn: Any, graph: Graph, root: Path | None) -> list[dict]:
    """Find rules from rules.json that affect this function's zone or file."""
    if root is None:
        return []
    rules_file = _load_rules(root)
    zone = graph.file_zone(fn.file)
    return [
        {"id": r.id, "category": r.category, "title": r.title}
        for r in rules_file.rules
        if zone in r.affects or fn.file in r.affects
    ]


def _build_callers_constraint(fn: Any, caller_edges: list) -> dict:
    return {
        "callers_expect": _signature(fn),
        "safe_changes": [
            "modify function body",
            "add optional parameters with defaults",
            "add overloaded variant",
        ],
        "breaking_changes": [
            "change existing parameter types",
            "change parameter order",
            "change return type",
            "remove parameters",
        ],
    }


# ---------------------------------------------------------------------------
# Per-fn / per-file shaping helpers — used by scope, browse, orient sections
# ---------------------------------------------------------------------------


def _impact_section_for_fn(fn, root: Path | None) -> dict | None:
    """Shape an impact section for scope(function=) from impact.json."""
    if root is None:
        return None
    impact = _load_impact(root)
    if impact is None:
        return None
    report = impact.functions.get(fn.id)
    if report is None:
        return None
    return {
        "risk_level": report.risk_level,
        "risk_score": report.risk_score,
        "summary": report.summary,
        "safe_operations": list(report.safe_operations),
        "dangerous_operations": list(report.dangerous_operations),
        "caller_classifications": [
            {
                "caller": cc.caller,
                "dependency_type": cc.dependency_type,
                "coupling": cc.coupling,
                "update_effort": cc.update_effort,
                "note": cc.note,
            }
            for cc in report.caller_classifications
        ],
        "action_plan": report.action_plan,
    }


def _similar_logic_for_fn(fn, graph: Graph) -> list[dict]:
    """Group other functions by shared secondary_intents with `fn`."""
    if not fn.secondary_intents:
        return []
    out: list[dict] = []
    for tag in fn.secondary_intents:
        others: list[str] = []
        for other in graph.functions.values():
            if other.id == fn.id:
                continue
            if tag in (other.secondary_intents or []):
                others.append(other.id)
        if not others:
            continue
        entry: dict = {"intent": tag, "also_in": others[:10]}
        if len(others) >= 2:
            entry["suggestion"] = f"consider extracting shared {tag} logic"
        out.append(entry)
    return out


def _value_locked_for_file(graph: Graph, file_path: str) -> list[dict]:
    """Compact value_locked_collections block for scope(file=) response."""
    out: list[dict] = []
    for c in graph.value_locked_collections:
        if c.file != file_path:
            continue
        entry: dict = {
            "name": c.name,
            "kind": c.kind,
            "values": list(c.values),
            "total_literal_uses": sum(c.literal_uses.values()),
        }
        if c.literal_uses:
            entry["literal_uses"] = dict(c.literal_uses)
        if c.files_with_uses:
            entry["files_with_uses"] = list(c.files_with_uses)
        out.append(entry)
    return out


def _file_fn_entry(graph: Graph, fid: str) -> dict:
    """One row inside scope(file=) functions[]. Includes intent if present."""
    fn = graph.functions[fid]
    entry: dict = {
        "id": fid,
        "name": fn.name,
        "locked": graph.is_locked(fid),
        "callers": len(graph.callers(fid)),
    }
    if fn.intent:
        entry["intent"] = fn.intent
    _attach_route(entry, fn)
    return entry


def _attach_route(entry: dict, fn) -> None:
    """Add route/http_method/template to a per-fn dict when fn is a handler."""
    if fn.route:
        entry["route"] = fn.route
        if fn.http_method:
            entry["http_method"] = fn.http_method
        if fn.template:
            entry["template"] = fn.template


def _route_marker(fn) -> str:
    """Compact '[METHOD /path]' marker for inline use in browse strings."""
    if not fn.route:
        return ""
    method = fn.http_method or "GET"
    return f"[{method} {fn.route}]"
