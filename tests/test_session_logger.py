"""Tests for per-Claude-session runtime directory + hook invocation logger."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from winkers.hooks._logger import HOOKS_LOG_NAME, log_hook
from winkers.session.session_dir import (
    SESSION_GC_KEEP,
    gc_old_sessions,
    get_session_dir,
    sessions_root,
)

# ---------------------------------------------------------------------------
# session_dir
# ---------------------------------------------------------------------------


class TestGetSessionDir:
    def test_creates_dir(self, tmp_path: Path) -> None:
        sess = get_session_dir(tmp_path, "abc-123")
        assert sess.is_dir()
        assert sess == tmp_path / ".winkers" / "sessions" / "abc-123"

    def test_falls_back_to_no_id(self, tmp_path: Path) -> None:
        sess = get_session_dir(tmp_path, "")
        assert sess.is_dir()
        assert sess.name == "no-id"

    def test_sanitizes_unsafe_chars(self, tmp_path: Path) -> None:
        sess = get_session_dir(tmp_path, "../../etc/passwd")
        assert ".." not in sess.name
        assert sess.parent == tmp_path / ".winkers" / "sessions"

    def test_idempotent(self, tmp_path: Path) -> None:
        a = get_session_dir(tmp_path, "same-id")
        b = get_session_dir(tmp_path, "same-id")
        assert a == b
        assert a.is_dir()


class TestGcOldSessions:
    def _make_session(self, root: Path, sid: str, age_seconds: float) -> Path:
        sess = get_session_dir(root, sid)
        old_time = time.time() - age_seconds
        os.utime(sess, (old_time, old_time))
        return sess

    def test_removes_old(self, tmp_path: Path) -> None:
        old = self._make_session(tmp_path, "old", age_seconds=10 * 24 * 3600)
        recent = self._make_session(tmp_path, "recent", age_seconds=60)
        removed = gc_old_sessions(tmp_path)
        assert removed == 1
        assert not old.exists()
        assert recent.exists()

    def test_keep_threshold(self, tmp_path: Path) -> None:
        # Make SESSION_GC_KEEP+5 recent dirs; oldest 5 should be removed.
        for i in range(SESSION_GC_KEEP + 5):
            sess = get_session_dir(tmp_path, f"sid-{i:03d}")
            # Stagger mtimes so the GC has a deterministic order.
            t = time.time() - (SESSION_GC_KEEP + 5 - i)
            os.utime(sess, (t, t))
        removed = gc_old_sessions(tmp_path)
        assert removed == 5
        remaining = list(sessions_root(tmp_path).iterdir())
        assert len(remaining) == SESSION_GC_KEEP

    def test_ignores_json_files(self, tmp_path: Path) -> None:
        # Recorded sessions live as flat *.json next to runtime dirs.
        # GC must never touch them.
        sessions_root(tmp_path).mkdir(parents=True, exist_ok=True)
        recorded = sessions_root(tmp_path) / "2026-04-29_a1b2c3d4.json"
        recorded.write_text("{}")
        old = self._make_session(tmp_path, "old", age_seconds=10 * 24 * 3600)

        gc_old_sessions(tmp_path)

        assert recorded.exists()
        assert not old.exists()

    def test_empty_root_no_error(self, tmp_path: Path) -> None:
        assert gc_old_sessions(tmp_path) == 0


# ---------------------------------------------------------------------------
# log_hook
# ---------------------------------------------------------------------------


def _read_log(root: Path, session_id: str) -> list[dict]:
    log_path = (
        root / ".winkers" / "sessions" / (session_id or "no-id") / HOOKS_LOG_NAME
    )
    assert log_path.exists(), f"expected log at {log_path}"
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


class TestLogHook:
    def test_writes_line_on_clean_exit(self, tmp_path: Path) -> None:
        with log_hook(tmp_path, "sid", "PreToolUse", "pre_write") as rec:
            rec["file"] = "app/foo.py"
        records = _read_log(tmp_path, "sid")
        assert len(records) == 1
        assert records[0]["event"] == "PreToolUse"
        assert records[0]["hook"] == "pre_write"
        assert records[0]["file"] == "app/foo.py"
        assert records[0]["outcome"] == "ok"
        assert "duration_ms" in records[0]

    def test_writes_line_on_sys_exit_zero(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            with log_hook(tmp_path, "sid", "Stop", "session_audit") as rec:
                rec["status"] = "PASS"
                raise SystemExit(0)
        records = _read_log(tmp_path, "sid")
        assert records[0]["outcome"] == "ok"
        assert records[0]["status"] == "PASS"

    def test_writes_line_on_sys_exit_nonzero(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            with log_hook(tmp_path, "sid", "PreToolUse", "pre_write") as rec:
                rec["decision"] = "deny"
                raise SystemExit(2)
        records = _read_log(tmp_path, "sid")
        assert records[0]["outcome"] == "exit_2"
        assert records[0]["decision"] == "deny"

    def test_writes_line_on_exception(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError):
            with log_hook(tmp_path, "sid", "PostToolUse", "post_write"):
                raise RuntimeError("boom")
        records = _read_log(tmp_path, "sid")
        assert "error: RuntimeError" in records[0]["outcome"]

    def test_appends_multiple_lines(self, tmp_path: Path) -> None:
        for i in range(3):
            with log_hook(tmp_path, "sid", "PreToolUse", "pre_write") as rec:
                rec["i"] = i
        records = _read_log(tmp_path, "sid")
        assert [r["i"] for r in records] == [0, 1, 2]

    def test_falls_back_to_no_id(self, tmp_path: Path) -> None:
        with log_hook(tmp_path, "", "Stop", "session_audit"):
            pass
        records = _read_log(tmp_path, "")
        assert records[0]["session_id"] == ""

    def test_filesystem_failure_does_not_break_hook(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Simulate a write failure: monkey-patch the writer to throw.
        from winkers.hooks import _logger

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(_logger, "_write_record", boom)

        # Hook body must complete normally even though logging exploded.
        with log_hook(tmp_path, "sid", "PreToolUse", "pre_write") as rec:
            rec["file"] = "app/foo.py"
