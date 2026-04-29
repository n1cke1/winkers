"""Stop hook — session audit recorder.

Wave 6 redesign: this hook **never** forces continuation. It runs the
3-tier audit (PASS / WARN / FAIL) once, persists `audit.json` into
the per-session directory, and writes a pending-audit markdown for
the next session's `prompt_enrich` to surface. Stop is always clean.

The agent can call ``session_done()`` itself before Stop to inspect
the audit early and fix issues — that path returns the same verdict
without writing files (the writes happen in this hook). FAIL or WARN
status is informational; the redesign explicitly drops "loop until
PASS" semantics (CONCEPT.md §5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from winkers.hooks._logger import log_hook
from winkers.session.audit import write_audit, write_pending_audit


def run(root: Path) -> None:
    """Read hook JSON from stdin, run session_done audit, persist + exit."""
    try:
        hook_data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    session_id = str(hook_data.get("session_id", ""))

    with log_hook(root, session_id, "Stop", "session_audit") as rec:
        from winkers.session.state import SessionStore

        session_store = SessionStore(root)
        session = session_store.load()

        # No session → nothing to audit. Still log so we can see Stop fire.
        if session is None or not session.writes:
            rec["outcome"] = "no_writes"
            sys.exit(0)

        rec["writes"] = len(session.writes)

        from winkers.store import GraphStore

        store = GraphStore(root)
        graph = store.load()
        if graph is None:
            rec["outcome"] = "no_graph"
            sys.exit(0)

        from winkers.mcp.tools import _tool_session_done

        audit = _tool_session_done(graph, root)
        status = audit.get("status", "PASS")
        rec["status"] = status

        # Persist to per-session dir + project-root pending file.
        audit_path = write_audit(root, session_id, audit)
        if audit_path is not None:
            rec["audit_path"] = str(
                audit_path.relative_to(root) if audit_path.is_relative_to(root)
                else audit_path
            )
        write_pending_audit(root, audit)

        # Surface a short additionalContext for the user/transcript.
        # No `continue: True` — Stop hook always allows the session to
        # end. Wave 6 dropped force-continuation: FAIL is informational,
        # not a forcing function.
        lines = [f"[Winkers] Session audit: {status}."]
        if status != "PASS":
            issues = audit.get("issues", [])[:3]
            for it in issues:
                kind = it.get("kind", "issue")
                detail = it.get("detail", "")
                lines.append(f"  ✗ {kind}: {detail}")
            warnings_list = audit.get("warnings", [])[:3]
            for w in warnings_list:
                kind = w.get("kind", "warn")
                detail = w.get("detail", "")
                lines.append(f"  ⚠ {kind}: {detail}")
            lines.append(
                "  (next session's prompt enrichment will surface this)"
            )

        output = {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": "\n".join(lines),
            },
        }
        print(json.dumps(output))
        sys.exit(0)
