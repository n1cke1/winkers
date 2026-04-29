"""Winkers MCP tools — orient, scope, convention_read, rule_read,
before_create, browse, impact_check, session_done, find_work_area.

`register_tools` wires the 9 tool modules into the MCP server. Each
module owns its own `Tool(...)` schema (`TOOL` constant) and `_tool_*`
implementation; this file is just the dispatcher.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent

from winkers.mcp.tools import (
    before_create,
    browse,
    convention_read,
    find_work_area,
    impact_check,
    orient,
    rule_read,
    scope,
    session_done,
)
from winkers.mcp.tools._common import (  # noqa: F401  (re-exports for external callers)
    MAX_ORIENT_TOKENS,
    _attach_route,
    _build_callers_constraint,
    _coerce_include,
    _estimate_tokens,
    _file_fn_entry,
    _find_function,
    _get_hotspots,
    _impact_section_for_fn,
    _load_impact,
    _load_rules,
    _load_semantic,
    _log_call,
    _one_liner,
    _recent_changes_from_graph,
    _related_rules,
    _route_marker,
    _semantic_context_for_fn,
    _signature,
    _similar_logic_for_fn,
    _try_compact,
    _value_locked_for_file,
    _zone_imported_by,
    _zone_imports_from,
)
from winkers.models import Graph

# Tool dispatch — each entry adapts a tool's per-signature `_tool_*`
# call to the uniform `(graph, args, root, get_graph)` shape used by
# the MCP `call_tool` handler. Order also drives `list_tools` output.
_TOOL_MODULES = [
    (orient, lambda g, a, r, gg: orient._tool_orient(g, a, r)),
    (scope, lambda g, a, r, gg: scope._tool_scope(g, a, r)),
    (convention_read, lambda g, a, r, gg: convention_read._tool_convention_read(a, r)),
    (rule_read, lambda g, a, r, gg: rule_read._tool_rule_read(a, r)),
    (before_create, lambda g, a, r, gg: before_create._tool_before_create(g, a, r)),
    (browse, lambda g, a, r, gg: browse._tool_browse(g, a)),
    (impact_check, lambda g, a, r, gg: impact_check._tool_impact_check(g, a, r, gg)),
    (find_work_area, lambda g, a, r, gg: find_work_area._tool_find_work_area(g, a, r)),
    (session_done, lambda g, a, r, gg: session_done._tool_session_done(g, r)),
]
_DISPATCH = {m.TOOL.name: fn for m, fn in _TOOL_MODULES}


def register_tools(
    server: Server,
    root: Path,
    get_graph: Callable[[], Graph | None],
) -> None:

    @server.list_tools()
    async def list_tools():
        return [m.TOOL for m, _ in _TOOL_MODULES]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json

        _log_call(root, name, arguments)
        graph = get_graph()

        if graph is None:
            return [TextContent(
                type="text",
                text='{"error": "Graph not initialized. Run winkers init first."}',
            )]

        handler = _DISPATCH.get(name)
        if handler is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            result = handler(graph, arguments, root, get_graph)

        return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Re-exports — extracted tool modules.
# ---------------------------------------------------------------------------

from winkers.mcp.tools.before_create import (  # noqa: E402, F401
    _before_create_change,
    _before_create_unknown,
    _duplication_warning,
    _files_block,
    _functions_block,
    _tool_before_create,
    _value_changes_block,
)
from winkers.mcp.tools.browse import _tool_browse  # noqa: E402, F401
from winkers.mcp.tools.convention_read import _tool_convention_read  # noqa: E402, F401
from winkers.mcp.tools.find_work_area import (  # noqa: E402, F401
    _find_work_area_advice,
    _tool_find_work_area,
)
from winkers.mcp.tools.impact_check import (  # noqa: E402, F401
    _coherence_check,
    _generate_incremental_intents,
    _tool_impact_check,
)
from winkers.mcp.tools.orient import (  # noqa: E402, F401
    _section_conventions,
    _section_functions_graph,
    _section_hotspots,
    _section_map,
    _section_routes,
    _section_rules_list,
    _section_ui_map,
    _session_status,
    _tool_orient,
    _validate_task,
)
from winkers.mcp.tools.rule_read import _tool_rule_read  # noqa: E402, F401
from winkers.mcp.tools.scope import _tool_scope  # noqa: E402, F401
from winkers.mcp.tools.session_done import (  # noqa: E402, F401
    _broken_caller_details,
    _check_complexity_delta,
    _extract_sync_files,
    _tool_session_done,
)
