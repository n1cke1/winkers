"""Tests for winkers.session.state — session tracking."""

import tempfile
from pathlib import Path

from winkers.session.state import SessionState, SessionStore, Warning, WriteEvent


class TestSessionState:
    def test_add_write(self):
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        event = WriteEvent(
            timestamp="2026-01-01T00:01:00Z",
            file_path="calc.py",
            functions_added=["new_func"],
        )
        state.add_write(event)
        assert len(state.writes) == 1
        assert state.impact_check_calls == 1

    def test_add_warning(self):
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        warning = Warning(
            kind="broken_caller",
            severity="error",
            target="calc.py::add",
            detail="Signature changed, 2 callers affected",
        )
        state.add_warning(warning)
        assert len(state.warnings) == 1

    def test_pending_warnings(self):
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="a", detail="issue 1",
        ))
        state.add_warning(Warning(
            kind="coherence", severity="warning",
            target="b", detail="issue 2", resolved=True,
        ))
        assert len(state.pending_warnings()) == 1

    def test_files_modified(self):
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_write(WriteEvent(
            timestamp="t1", file_path="a.py",
        ))
        state.add_write(WriteEvent(
            timestamp="t2", file_path="b.py",
        ))
        state.add_write(WriteEvent(
            timestamp="t3", file_path="a.py",
        ))
        assert set(state.files_modified()) == {"a.py", "b.py"}

    def test_summary(self):
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_write(WriteEvent(timestamp="t1", file_path="a.py"))
        state.add_warning(Warning(
            kind="coherence", severity="warning", target="a.py", detail="check sync",
        ))
        summary = state.summary()
        assert summary["writes"] == 1
        assert summary["warnings_total"] == 1
        assert summary["warnings_pending"] == 1
        assert summary["files_modified"] == 1


class TestSessionStore:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".winkers").mkdir()

            store = SessionStore(root)
            state = SessionState(started_at="2026-01-01T00:00:00Z")
            state.add_write(WriteEvent(
                timestamp="t1", file_path="test.py",
                functions_added=["foo"],
            ))
            state.add_warning(Warning(
                kind="broken_caller", severity="error",
                target="test.py::bar", detail="sig changed",
            ))
            store.save(state)

            loaded = store.load()
            assert loaded is not None
            assert len(loaded.writes) == 1
            assert len(loaded.warnings) == 1
            assert loaded.started_at == "2026-01-01T00:00:00Z"

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir))
            assert store.load() is None

    def test_load_or_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir))
            state = store.load_or_create()
            assert state.started_at != ""
            assert state.writes == []

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".winkers").mkdir()

            store = SessionStore(root)
            state = SessionState(started_at="2026-01-01T00:00:00Z")
            store.save(state)
            assert store.load() is not None

            store.clear()
            assert store.load() is None


class TestWarningModel:
    def test_coherence_warning_with_fix_approach(self):
        w = Warning(
            kind="coherence",
            severity="warning",
            target="engine/equations.py",
            detail="Rule #14: check templates/index.html",
            fix_approach="derived",
        )
        assert w.fix_approach == "derived"
        assert not w.resolved

    def test_default_fix_approach_is_none(self):
        w = Warning(
            kind="broken_caller",
            severity="error",
            target="calc.py::add",
            detail="sig changed",
        )
        assert w.fix_approach is None
