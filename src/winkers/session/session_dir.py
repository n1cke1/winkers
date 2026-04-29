"""Per-Claude-session directory management.

Claude Code passes `session_id` in every hook payload via stdin. Each
session gets its own directory under `.winkers/sessions/<id>/` for:

  * `hooks.log` — append-only JSONL of every hook invocation
  * `intents.json` — registered before_create calls (Wave 6)
  * `seen_units.json` — context dedup (Wave 7)
  * `audit.json` — final audit verdict (Wave 6)

GC is run from `winkers init`: directories older than TTL are removed
along with anything beyond the count threshold (oldest first).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from winkers.store import STORE_DIR

SESSIONS_DIR = "sessions"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_GC_KEEP = 50

_UNSAFE = ("/", "\\", "..", "\x00")
_NO_ID = "no-id"


def _safe_id(session_id: str) -> str:
    if not session_id:
        return _NO_ID
    safe = session_id.strip()
    for ch in _UNSAFE:
        safe = safe.replace(ch, "_")
    return safe or _NO_ID


def get_session_dir(root: Path, session_id: str) -> Path:
    """Return per-session directory, creating it if absent."""
    path = root / STORE_DIR / SESSIONS_DIR / _safe_id(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sessions_root(root: Path) -> Path:
    return root / STORE_DIR / SESSIONS_DIR


def gc_old_sessions(root: Path) -> int:
    """Remove session dirs older than TTL or beyond keep-count.

    Returns the number of directories removed. Never raises — GC must
    not block `winkers init` if the directory tree is unexpected.
    """
    base = sessions_root(root)
    if not base.exists():
        return 0

    candidates = [p for p in base.iterdir() if p.is_dir()]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    now = time.time()
    removed = 0
    for index, dir_path in enumerate(candidates):
        try:
            age = now - dir_path.stat().st_mtime
            too_old = age > SESSION_TTL_SECONDS
            beyond_keep = index >= SESSION_GC_KEEP
            if too_old or beyond_keep:
                shutil.rmtree(dir_path)
                removed += 1
        except Exception:
            continue

    return removed
