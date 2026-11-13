"""PreToolUse hook for the Graphify benchmark adapter.

Invoked by Claude Code before Glob|Grep tool calls. Emits
`hookSpecificOutput.additionalContext` nudging the agent toward the
graphify MCP tools when a graph.json exists in the project root.

Why a standalone script (vs `python -c "..."` one-liner in settings.json):
- Avoids shell/JSON escaping round-trips that mangled our inline version
- Uses sys.stdout.buffer to bypass Windows text-mode CRLF translation
- Has room for the diagnostic marker-file log used during debugging

Writes a trace marker to c:/tmp/gfy_hook_trace.log on every invocation
so we can verify Claude Code actually fires the hook (and not only that
the JSON injection works).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(os.getcwd())
GRAPH_JSON = PROJECT_ROOT / "graphify-out" / "graph.json"
MARKER = Path("c:/tmp/gfy_hook_trace.log")

try:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    with MARKER.open("a", encoding="utf-8") as f:
        f.write(
            f"{datetime.datetime.now().isoformat()} "
            f"cwd={PROJECT_ROOT} graph_exists={GRAPH_JSON.exists()}\n"
        )
except Exception:
    pass

if not GRAPH_JSON.exists():
    sys.exit(0)

# Use winkers-style PreToolUse format: permissionDecision + permissionDecisionReason.
# Empirically: additionalContext in PreToolUse is documented but our Claude Code
# version silently drops it (marker fires but text never appears in
# claude_output.json). Winkers ships with the permissionDecision pattern in
# its pre_write hook and that variant is known to inject context. We allow
# the tool (never block) and use the reason field as the nudge vehicle.
output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": (
            "graphify: Knowledge graph exists at graphify-out/graph.json. "
            "Before grep/glob, consider mcp__graphify__query_graph "
            "(BFS/DFS from a question) or mcp__graphify__god_nodes "
            "(core abstractions) for precise architecture navigation."
        ),
    }
}

sys.stdout.buffer.write((json.dumps(output) + "\n").encode("utf-8"))
sys.stdout.buffer.flush()
