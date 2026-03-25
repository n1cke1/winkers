"""Save and load session records in .winkers/sessions/."""

from __future__ import annotations

import json
from pathlib import Path

from winkers.models import ScoredSession
from winkers.store import STORE_DIR

SESSIONS_DIR = "sessions"


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions_dir = root / STORE_DIR / SESSIONS_DIR

    def save(self, scored: ScoredSession) -> Path:
        """Save a scored session, return the file path."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        ts = scored.session.started_at[:10]  # YYYY-MM-DD
        hash_prefix = scored.session.task_hash[:8]
        filename = f"{ts}_{hash_prefix}.json"
        path = self.sessions_dir / filename

        # Avoid overwriting — append counter if exists
        counter = 1
        while path.exists():
            filename = f"{ts}_{hash_prefix}_{counter}.json"
            path = self.sessions_dir / filename
            counter += 1

        path.write_text(
            scored.model_dump_json(indent=2), encoding="utf-8",
        )
        return path

    def load_all(self) -> list[ScoredSession]:
        """Load all saved sessions."""
        if not self.sessions_dir.exists():
            return []
        sessions = []
        for f in sorted(self.sessions_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append(ScoredSession.model_validate(data))
            except Exception:
                continue
        return sessions

    def recorded_session_ids(self) -> set[str]:
        """Return set of already-recorded session IDs."""
        ids: set[str] = set()
        for scored in self.load_all():
            ids.add(scored.session.session_id)
        return ids

    def find_by_task_hash(self, task_hash: str) -> list[ScoredSession]:
        """Find sessions with the same task hash (for redo detection)."""
        return [s for s in self.load_all() if s.session.task_hash == task_hash]
