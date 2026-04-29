"""MCP tool: browse — paginated function inventory by zone/file with caller call-sites."""

from __future__ import annotations

from typing import Any

from winkers.mcp.tools._common import _route_marker
from winkers.models import Graph

_BROWSE_LIMIT_DEFAULT = 50
_BROWSE_LIMIT_MAX = 100
_BROWSE_FILE_HINT_LIMIT = 20


def _tool_browse(graph: Graph, args: dict) -> dict:
    """List functions with their LLM-generated intents (mid-level inventory).

    Sits between `orient` (zone-level map) and `scope` (single-function
    deep-dive) — lets an agent skim "what functions exist here and what do
    they do" before picking a target.

    Args:
      zone        : filter by zone name (see orient.map.zones).
      file        : filter by exact file path (supersedes zone if both).
      min_callers : hide fns with fewer callers than N (default 0).
      limit       : page size (default 50, max 100).
      offset      : starting index within the sorted list (default 0).

    Response entries are compact strings:
      "file::fn (callers) — intent"          if intent present
      "file::fn (callers)"                   if intent null
    """
    zone = args.get("zone") or None
    file_path = args.get("file") or None
    min_callers = int(args.get("min_callers", 0) or 0)
    limit = int(args.get("limit", _BROWSE_LIMIT_DEFAULT) or _BROWSE_LIMIT_DEFAULT)
    offset = int(args.get("offset", 0) or 0)

    if limit < 1:
        limit = _BROWSE_LIMIT_DEFAULT
    if limit > _BROWSE_LIMIT_MAX:
        limit = _BROWSE_LIMIT_MAX
    if offset < 0:
        offset = 0

    zone_norm = zone.rstrip("/\\").replace("\\", "/") if zone else None

    def _keep(fn_id: str) -> bool:
        fn = graph.functions[fn_id]
        if file_path and fn.file != file_path:
            return False
        if zone_norm:
            # Accept either the stored zone label (e.g. "modules") OR a path
            # prefix (e.g. "app/services") — projects whose graph carries only
            # top-level zone labels can still drill down by subdirectory.
            fn_file_norm = fn.file.replace("\\", "/")
            if (
                graph.file_zone(fn.file) != zone_norm
                and fn_file_norm != zone_norm
                and not fn_file_norm.startswith(zone_norm + "/")
            ):
                return False
        if min_callers and len(graph.callers(fn_id)) < min_callers:
            return False
        return True

    matched = sorted(fid for fid in graph.functions if _keep(fid))
    total = len(matched)
    page_ids = matched[offset : offset + limit]

    # When filtering by file the page is small enough that we can interleave
    # caller call-sites under each function — turning browse(file=…) into a
    # one-stop view for "who calls what I'm about to edit". Per-fn cost is
    # ~15 tok per caller, still well under typical orient-level budgets.
    show_call_sites = bool(file_path)

    lines: list[str] = []
    for fid in page_ids:
        fn = graph.functions[fid]
        caller_edges = graph.callers(fid)
        route_marker = _route_marker(fn)
        entry = f"{fid} ({len(caller_edges)})"
        if route_marker:
            entry = f"{entry} {route_marker}"
        if fn.intent:
            entry = f"{entry} — {fn.intent}"
        lines.append(entry)
        if show_call_sites:
            for e in caller_edges:
                lines.append(
                    f"  ← {e.call_site.file}:{e.call_site.line}  "
                    f"{e.call_site.expression}"
                )

    # `shown` counts functions, not interleaved caller lines — paging stays
    # predictable when show_call_sites expands the list.
    shown_fns = len(page_ids)
    result: dict[str, Any] = {
        "total": total,
        "shown": shown_fns,
        "offset": offset,
        "functions": lines,
    }
    if offset + shown_fns < total:
        result["next_offset"] = offset + shown_fns
    if zone:
        result["zone"] = zone
    if file_path:
        result["file"] = file_path
    if min_callers:
        result["min_callers"] = min_callers
    if total == 0:
        filters = []
        if zone:
            filters.append(f"zone={zone!r}")
        if file_path:
            filters.append(f"file={file_path!r}")
        if min_callers:
            filters.append(f"min_callers={min_callers}")
        filter_echo = f" Filters applied: {', '.join(filters)}." if filters else ""

        # When a zone was requested but yielded nothing, surface the list of
        # files that live under that zone/prefix. Actionable: the agent can
        # immediately drill down with browse(file=…). If min_callers is the
        # culprit (zone has fns but all below threshold) we stay silent on
        # files so the real hint is to lower the threshold.
        zone_files: list[str] = []
        if zone_norm and not min_callers:
            for path in sorted(graph.files):
                fn_file_norm = path.replace("\\", "/")
                if (
                    graph.file_zone(path) == zone_norm
                    or fn_file_norm == zone_norm
                    or fn_file_norm.startswith(zone_norm + "/")
                ):
                    zone_files.append(path)
                    if len(zone_files) >= _BROWSE_FILE_HINT_LIMIT:
                        break
        if zone_files:
            result["files_in_zone"] = zone_files
            result["hint"] = (
                "No functions match." + filter_echo
                + " The zone contains files listed in `files_in_zone` —"
                " drill down with browse(file=<path>)."
            )
        else:
            result["hint"] = (
                "No functions match." + filter_echo
                + " Try orient(include=['map']) to see valid zone names,"
                " drop the zone/file filter, or lower min_callers."
            )
    return result
