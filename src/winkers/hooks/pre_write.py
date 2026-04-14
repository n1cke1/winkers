"""PreToolUse(Write|Edit) hook — AST hash duplicate gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run(root: Path) -> None:
    """Read hook JSON from stdin, check for exact clones, deny or allow."""
    try:
        hook_data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = hook_data.get("tool_name", "")
    tool_input = hook_data.get("tool_input", {})

    # Only check Write/Edit/MultiEdit tools
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    # Normalize to relative path
    try:
        rel_path = str(Path(file_path).relative_to(root)).replace("\\", "/")
    except ValueError:
        rel_path = file_path.replace("\\", "/")

    # Load graph
    from winkers.store import GraphStore

    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        sys.exit(0)

    # Get content being written
    content = tool_input.get("file_text", "") or tool_input.get("content", "")
    if not content:
        sys.exit(0)

    # Check for exact clones using AST hash
    from winkers.detection.duplicates import find_duplicates

    # Get functions in the target file after potential write
    file_node = graph.files.get(rel_path)
    if file_node is None:
        sys.exit(0)

    new_fn_ids = file_node.function_ids
    if not new_fn_ids:
        sys.exit(0)

    duplicates = find_duplicates(graph, new_fn_ids)
    exact_clones = [d for d in duplicates if d.kind == "exact"]
    near_clones = [d for d in duplicates if d.kind == "near"]

    if exact_clones:
        # Deny with import suggestion
        clone = exact_clones[0]
        msg = (
            f"[Winkers] BLOCKED: {clone.fn_a.name}() is an exact clone of "
            f"{clone.fn_b.name}() in {clone.fn_b.file}:{clone.fn_b.line_start}. "
            f"Import and reuse instead of duplicating."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg,
            }
        }), file=sys.stderr)
        sys.exit(2)

    if near_clones:
        # Allow with warning
        warnings = []
        for nc in near_clones[:3]:
            warnings.append(
                f"  - {nc.fn_a.name}() is similar to {nc.fn_b.name}() "
                f"in {nc.fn_b.file}:{nc.fn_b.line_start} "
                f"(similarity: {nc.similarity:.0%})"
            )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    "[Winkers] Similar functions found:\n" + "\n".join(warnings)
                    + "\nConsider reusing existing code."
                ),
            }
        }
        print(json.dumps(output))

    sys.exit(0)
