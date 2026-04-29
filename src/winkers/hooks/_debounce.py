"""Per-session content-hash cache for post-write hook debounce.

When the agent fires multiple `Write|Edit` events that produce the
same file bytes (idempotent reformat passes, MultiEdit no-op regions,
git checkout to a previously-cached state), we want to short-circuit
the impact pipeline. The graph already reflects that content; running
the full update again is wasted work.

State lives at `.winkers/sessions/<session_id>/post_write_hashes.json`
so the cache is per-Claude-session and disappears with the session
dir. A different session always re-runs at least once per file (cold
start), which is what we want.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from winkers.session.session_dir import get_session_dir

CACHE_FILENAME = "post_write_hashes.json"


def file_hash(path: Path) -> str | None:
    """Return SHA-256 hex of file bytes, or None on error."""
    try:
        with path.open("rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _cache_path(root: Path, session_id: str) -> Path:
    return get_session_dir(root, session_id) / CACHE_FILENAME


def _load(root: Path, session_id: str) -> dict[str, str]:
    path = _cache_path(root, session_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(root: Path, session_id: str, cache: dict[str, str]) -> None:
    path = _cache_path(root, session_id)
    try:
        path.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        return


def should_skip(
    root: Path,
    session_id: str,
    rel_path: str,
    current_hash: str,
) -> bool:
    """True iff `rel_path` was processed earlier this session with the
    exact same content hash."""
    cache = _load(root, session_id)
    return cache.get(rel_path) == current_hash


def remember(
    root: Path,
    session_id: str,
    rel_path: str,
    current_hash: str,
) -> None:
    """Record `current_hash` as the processed hash for `rel_path`."""
    cache = _load(root, session_id)
    cache[rel_path] = current_hash
    _save(root, session_id, cache)
