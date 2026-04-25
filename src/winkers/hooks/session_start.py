"""SessionStart hook: persist the current git HEAD as the audit baseline.

Called at the very beginning of a Claude Code session. Records the
commit hash to `.winkers/_session_start.txt` so the SessionEnd audit
can compute an honest diff between session-start and session-end —
not just the latest commit.

Silent on non-git checkouts; the SessionEnd audit will fall back to
HEAD~1 if the file is missing.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SESSION_START_FILE = "_session_start.txt"


def run(root: Path) -> None:
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
        return
    if not head:
        return
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(head, encoding="utf-8")
    except Exception as e:
        log.debug("session-start: cannot write %s (%s)", out, e)


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
