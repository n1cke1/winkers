"""Per-session audit persistence — Wave 6 of the redesign.

The Stop hook always exits cleanly (no forced continuation) and writes
the session's final verdict to:

  * ``.winkers/sessions/<session_id>/audit.json`` — durable record for
    telemetry / benchmark axis / future analysis.
  * ``.winkers_pending_audit.md`` (project root) — human-readable
    snapshot consumed by the *next* session's `prompt_enrich` so the
    agent sees ``previous session ended with FAIL — fix this first``
    on its first prompt.

Both writes are best-effort: on failure we log and move on. The audit
must never break the hook.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from winkers.session.session_dir import get_session_dir

log = logging.getLogger(__name__)

AUDIT_FILENAME = "audit.json"
PENDING_AUDIT_FILENAME = ".winkers_pending_audit.md"


def write_audit(
    root: Path,
    session_id: str,
    audit: dict,
) -> Path | None:
    """Persist `audit` JSON inside the per-session directory.

    Returns the file path on success, None on failure (logged but
    swallowed — caller must remain robust).
    """
    try:
        sess_dir = get_session_dir(root, session_id)
        path = sess_dir / AUDIT_FILENAME
        # Embellish with timestamp + session_id (callers usually only
        # provide the verdict + issues from _tool_session_done).
        payload = {
            "session_id": session_id or "no-id",
            "ts": datetime.now(UTC).isoformat(),
            **audit,
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path
    except Exception as e:
        log.debug("audit: cannot write audit.json (%s)", e)
        return None


def write_pending_audit(root: Path, audit: dict) -> Path | None:
    """Write a human-readable snapshot for the next session's prompt.

    Replaces the prior pending file — only one audit can be ``pending``
    at a time. The next session's `prompt_enrich._consume_pending_audit`
    archives + injects it once.
    """
    status = audit.get("status", "UNKNOWN")
    if status == "PASS":
        # No need to nag the next session — clear any stale pending.
        clear_pending_audit(root)
        return None
    try:
        path = root / PENDING_AUDIT_FILENAME
        body = _render_audit_md(audit)
        path.write_text(body, encoding="utf-8")
        return path
    except Exception as e:
        log.debug("audit: cannot write pending audit (%s)", e)
        return None


def consume_pending_audit(root: Path) -> str | None:
    """Read + archive the pending audit. Returns the markdown body or None."""
    path = root / PENDING_AUDIT_FILENAME
    if not path.exists():
        return None
    try:
        body = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None

    # Move to history so the same audit doesn't repeat on every prompt.
    try:
        history_dir = root / ".winkers" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        archive = history_dir / f"audit_{ts}.md"
        archive.write_text(body, encoding="utf-8")
        path.unlink(missing_ok=True)
    except Exception:
        # Best-effort: even if archival fails, we still want to clear
        # the pending file so the next prompt doesn't loop on it.
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    return body or None


def clear_pending_audit(root: Path) -> None:
    path = root / PENDING_AUDIT_FILENAME
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _render_audit_md(audit: dict) -> str:
    """Format the audit dict as compact markdown for prompt injection."""
    status = audit.get("status", "UNKNOWN")
    lines = [
        f"# Previous session audit: **{status}**",
        "",
    ]
    issues = audit.get("issues") or []
    if issues:
        lines.append("## Issues")
        for it in issues[:10]:
            kind = it.get("kind", "issue")
            detail = it.get("detail", "")
            lines.append(f"- **{kind}** — {detail}")
        lines.append("")

    warnings_list = audit.get("warnings") or []
    if warnings_list:
        lines.append("## Warnings")
        for w in warnings_list[:10]:
            kind = w.get("kind", "warning")
            detail = w.get("detail", "")
            lines.append(f"- **{kind}** — {detail}")
        lines.append("")

    recs = audit.get("recommendations") or []
    if recs:
        lines.append("## Recommendations")
        for r in recs[:10]:
            kind = r.get("kind", "rec")
            detail = r.get("detail", "")
            lines.append(f"- **{kind}** — {detail}")
        lines.append("")

    summary = audit.get("session") or {}
    if summary:
        lines.append("## Session summary")
        for k, v in summary.items():
            lines.append(f"- {k}: {v}")

    return "\n".join(lines).rstrip() + "\n"
