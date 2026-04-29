"""SessionStart hook: persist the current git HEAD as the audit baseline.

Called at the very beginning of a Claude Code session. Records the
commit hash to `.winkers/_session_start.txt` so the SessionEnd audit
can compute an honest diff between session-start and session-end —
not just the latest commit.

Silent on non-git checkouts; the SessionEnd audit will fall back to
HEAD~1 if the file is missing.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from winkers.hooks._logger import log_hook

log = logging.getLogger(__name__)

SESSION_START_FILE = "_session_start.txt"


def _read_session_id() -> str:
    """Read session_id from the hook stdin payload, if present."""
    try:
        payload = sys.stdin.read()
    except Exception:
        return ""
    if not payload:
        return ""
    try:
        return str(json.loads(payload).get("session_id", ""))
    except Exception:
        return ""


def run(root: Path) -> None:
    session_id = _read_session_id()

    with log_hook(root, session_id, "SessionStart", "session_start") as rec:
        out = root / ".winkers" / SESSION_START_FILE
        try:
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root),
                text=True,
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception as e:
            log.debug("session-start: cannot read git HEAD (%s)", e)
            rec["outcome"] = "no_git_head"
            return
        if not head:
            rec["outcome"] = "empty_head"
            return
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(head, encoding="utf-8")
            rec["baseline_commit"] = head[:8]
        except Exception as e:
            log.debug("session-start: cannot write %s (%s)", out, e)
            rec["outcome"] = f"write_failed: {e}"


def read_baseline(root: Path) -> str | None:
    """Return the saved session-start commit, or None if missing."""
    p = root / ".winkers" / SESSION_START_FILE
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def clear_baseline(root: Path) -> None:
    """Remove the saved session-start file once consumed by SessionEnd."""
    p = root / ".winkers" / SESSION_START_FILE
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
