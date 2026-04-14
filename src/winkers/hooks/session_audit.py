"""Stop hook — session audit gate. Forces continuation on first FAIL."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run(root: Path) -> None:
    """Read hook JSON from stdin, run session_done audit, output continue/stop."""
    try:
        json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    from winkers.session.state import SessionStore

    session_store = SessionStore(root)
    session = session_store.load()

    # No session — nothing to audit
    if session is None or not session.writes:
        sys.exit(0)

    from winkers.store import GraphStore

    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        sys.exit(0)

    # Import the audit logic from MCP tools
    from winkers.mcp.tools import _tool_session_done

    result = _tool_session_done(graph, root)
    status = result.get("status", "PASS")

    if status == "FAIL":
        # First FAIL → continue, give the agent a chance to fix
        issues_text = _format_issues(result.get("issues", []))
        output = {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": (
                    "[Winkers] SESSION AUDIT FAILED — fix before finishing:\n"
                    + issues_text
                    + "\nFix the issues above, then your task will be complete."
                ),
            },
        }
        print(json.dumps(output))
        sys.exit(0)

    # PASS (or second+ call via anti-loop) → allow stop
    lines = ["[Winkers] Session audit PASSED."]
    recommendations = result.get("recommendations", [])
    if recommendations:
        lines.append("Recommendations for future improvement:")
        for rec in recommendations[:3]:
            lines.append(f"  - {rec.get('detail', '')}")

    remaining = result.get("remaining_warnings", [])
    if remaining:
        lines.append("Remaining warnings (logged for improve loop):")
        for w in remaining[:3]:
            lines.append(f"  - {w}")

    output = {
        "continue": False,
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": "\n".join(lines),
        },
    }
    print(json.dumps(output))
    sys.exit(0)


def _format_issues(issues: list[dict]) -> str:
    """Format audit issues as readable text."""
    lines: list[str] = []
    for issue in issues:
        kind = issue.get("kind", "")
        detail = issue.get("detail", "")
        if kind == "broken_caller":
            lines.append(f"  ✗ BROKEN CALLERS: {detail}")
            for site in issue.get("call_sites", [])[:5]:
                f = site.get("file", "")
                ln = site.get("line", "")
                fn = site.get("fn", "")
                lines.append(f"    → {f}:{ln} ({fn})")
        elif kind == "coherence_sync":
            lines.append(f"  ✗ COHERENCE: {detail}")
            unmod = issue.get("unmodified_files", [])
            if unmod:
                lines.append(f"    Files not updated: {', '.join(unmod)}")
        elif kind == "debt_regression":
            lines.append(f"  ✗ DEBT: {detail}")
        else:
            lines.append(f"  ✗ {kind}: {detail}")
    return "\n".join(lines)
