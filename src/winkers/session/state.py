"""Session state — tracks agent activity during a coding session."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from winkers.store import STORE_DIR

SESSION_FILE = "session.json"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class WriteEvent(BaseModel):
    timestamp: str
    file_path: str
    functions_added: list[str] = []
    functions_modified: list[str] = []
    functions_removed: list[str] = []
    signature_changes: list[dict] = []  # [{fn_id, old_sig, new_sig}]


class Warning(BaseModel):
    kind: str  # "broken_caller" | "debt_regression" | "coherence"
    severity: str  # "error" | "warning"
    target: str  # fn_id or file path
    detail: str
    resolved: bool = False
    fix_approach: str | None = None  # "sync" | "derived" | "refactor" | None


class SessionState(BaseModel):
    started_at: str = ""
    writes: list[WriteEvent] = []
    warnings: list[Warning] = []
    before_create_calls: int = 0
    impact_check_calls: int = 0
    session_done_calls: int = 0
    graph_snapshot_at_start: dict[str, str] = {}  # {fn_id: signature_hash}

    def add_write(self, event: WriteEvent) -> None:
        self.writes.append(event)
        self.impact_check_calls += 1

    def add_warning(self, warning: Warning) -> None:
        self.warnings.append(warning)

    def pending_warnings(self) -> list[Warning]:
        return [w for w in self.warnings if not w.resolved]

    def files_modified(self) -> list[str]:
        return list({w.file_path for w in self.writes})

    def summary(self) -> dict:
        return {
            "writes": len(self.writes),
            "warnings_total": len(self.warnings),
            "warnings_pending": len(self.pending_warnings()),
            "files_modified": len(self.files_modified()),
            "before_create_calls": self.before_create_calls,
            "impact_check_calls": self.impact_check_calls,
        }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session_path = root / STORE_DIR / SESSION_FILE

    def load(self) -> SessionState | None:
        if not self.session_path.exists():
            return None
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
            return SessionState.model_validate(data)
        except Exception:
            return None

    def save(self, state: SessionState) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(
            state.model_dump_json(indent=2), encoding="utf-8"
        )

    def load_or_create(self) -> SessionState:
        state = self.load()
        if state is None:
            state = SessionState(
                started_at=datetime.now(UTC).isoformat(),
            )
        return state

    def clear(self) -> None:
        if self.session_path.exists():
            self.session_path.unlink()
