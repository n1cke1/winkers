"""Wave 6 — 3-tier audit verdict + audit.json persistence + prompt_enrich pickup."""

from __future__ import annotations

import json
from pathlib import Path

from winkers.graph import GraphBuilder
from winkers.mcp.tools import _tool_session_done
from winkers.resolver import CrossFileResolver
from winkers.session.audit import (
    AUDIT_FILENAME,
    PENDING_AUDIT_FILENAME,
    consume_pending_audit,
    write_audit,
    write_pending_audit,
)
from winkers.session.session_dir import get_session_dir
from winkers.session.state import SessionState, SessionStore, Warning, WriteEvent

PYTHON_FIXTURE = (
    Path(__file__).parent / "fixtures" / "python_project"
)


def _seed_graph(tmp_path: Path):
    """Build a graph fixture so _tool_session_done has something to read."""
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# WARN tier
# ---------------------------------------------------------------------------


class TestWarnTier:
    def test_writes_without_before_create_warns(self, tmp_path: Path):
        """Wave 6 — terra incognita: writes happened, no before_create."""
        (tmp_path / ".winkers").mkdir()
        graph = _seed_graph(tmp_path)
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_write(WriteEvent(timestamp="t1", file_path="a.py"))
        # NB: state.before_create_calls stays at 0
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "WARN"
        kinds = [w["kind"] for w in result.get("warnings", [])]
        assert "no_intent_registered" in kinds

    def test_value_locked_warning_is_warn(self, tmp_path: Path):
        """Pending value_locked warning → WARN (not FAIL)."""
        (tmp_path / ".winkers").mkdir()
        graph = _seed_graph(tmp_path)
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="value_locked", severity="warning",
            target="status.py::VALID_STATUSES",
            detail="VALID_STATUSES: removed ['draft']; 0 call-site uses.",
        ))
        # Pretend an intent was registered so 'no_intent_registered'
        # warning doesn't shadow the value_locked WARN check.
        state.before_create_calls = 1
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "WARN"
        kinds = [w["kind"] for w in result.get("warnings", [])]
        assert "value_locked" in kinds

    def test_fail_overrides_warn(self, tmp_path: Path):
        """Both FAIL-level issue and WARN signal → status is FAIL."""
        (tmp_path / ".winkers").mkdir()
        graph = _seed_graph(tmp_path)
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        # FAIL signal
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="modules/pricing.py::calculate_price",
            detail="signature changed",
        ))
        # WARN signal alongside
        state.add_warning(Warning(
            kind="value_locked", severity="warning",
            target="x::S", detail="some value warn",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "FAIL"
        # Both surfaces still appear
        assert result.get("issues")
        assert result.get("warnings")


# ---------------------------------------------------------------------------
# audit.json persistence
# ---------------------------------------------------------------------------


class TestAuditPersistence:
    def test_write_audit_creates_file(self, tmp_path: Path):
        path = write_audit(
            tmp_path, "sid-123",
            {"status": "FAIL", "issues": [{"kind": "x", "detail": "y"}]},
        )
        assert path is not None
        assert path == get_session_dir(tmp_path, "sid-123") / AUDIT_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "FAIL"
        assert data["session_id"] == "sid-123"
        assert "ts" in data

    def test_write_audit_handles_missing_session_id(self, tmp_path: Path):
        path = write_audit(tmp_path, "", {"status": "PASS"})
        assert path is not None
        assert path.parent.name == "no-id"

    def test_write_pending_audit_only_for_warn_or_fail(self, tmp_path: Path):
        # PASS → no pending file (and any prior pending is cleared)
        (tmp_path / PENDING_AUDIT_FILENAME).write_text("stale", encoding="utf-8")
        out = write_pending_audit(tmp_path, {"status": "PASS"})
        assert out is None
        assert not (tmp_path / PENDING_AUDIT_FILENAME).exists()

        # FAIL → pending file written
        out = write_pending_audit(
            tmp_path,
            {
                "status": "FAIL",
                "issues": [{"kind": "broken_caller", "detail": "x"}],
            },
        )
        assert out is not None
        body = (tmp_path / PENDING_AUDIT_FILENAME).read_text()
        assert "FAIL" in body
        assert "broken_caller" in body

        # WARN → pending file written
        out = write_pending_audit(
            tmp_path,
            {
                "status": "WARN",
                "warnings": [{"kind": "value_locked", "detail": "y"}],
            },
        )
        assert out is not None
        body = (tmp_path / PENDING_AUDIT_FILENAME).read_text()
        assert "WARN" in body
        assert "value_locked" in body

    def test_consume_pending_audit_archives_and_clears(self, tmp_path: Path):
        write_pending_audit(
            tmp_path,
            {"status": "FAIL", "issues": [{"kind": "x", "detail": "y"}]},
        )
        # First read returns the body and archives
        body = consume_pending_audit(tmp_path)
        assert body is not None
        assert "FAIL" in body
        # Pending file is gone
        assert not (tmp_path / PENDING_AUDIT_FILENAME).exists()
        # Archived under .winkers/history/
        history = list((tmp_path / ".winkers" / "history").glob("audit_*.md"))
        assert len(history) == 1
        # Second read returns None (already consumed)
        assert consume_pending_audit(tmp_path) is None

    def test_consume_missing_returns_none(self, tmp_path: Path):
        assert consume_pending_audit(tmp_path) is None
