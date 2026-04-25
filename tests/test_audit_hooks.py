"""Tests for SessionStart hook + prompt-enrich pending integration."""

from __future__ import annotations

import subprocess

from winkers.hooks.prompt_enrich import (
    EMPTY_PENDING_MARKER,
    _consume_pending,
)
from winkers.hooks.session_start import (
    clear_baseline,
    read_baseline,
)

# ---------------------------------------------------------------------------
# session_start
# ---------------------------------------------------------------------------

def test_session_start_writes_commit_to_file(tmp_path, monkeypatch):
    """When git HEAD resolves, the file is created with the commit hash."""
    fake_head = "0123456789abcdef" * 2 + "00000000"

    def fake_check_output(*args, **kwargs):
        return fake_head + "\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    from winkers.hooks.session_start import run
    run(tmp_path)
    assert read_baseline(tmp_path) == fake_head


def test_session_start_silent_on_non_git(tmp_path, monkeypatch):
    """No git HEAD → no file written, no exception raised."""
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(subprocess, "check_output", fail)

    from winkers.hooks.session_start import run
    run(tmp_path)
    assert read_baseline(tmp_path) is None


def test_clear_baseline_removes_file(tmp_path):
    p = tmp_path / ".winkers" / "_session_start.txt"
    p.parent.mkdir(parents=True)
    p.write_text("commit", encoding="utf-8")
    clear_baseline(tmp_path)
    assert not p.exists()


def test_clear_baseline_silent_when_missing(tmp_path):
    """Calling clear when file doesn't exist must not raise."""
    clear_baseline(tmp_path)


# ---------------------------------------------------------------------------
# prompt_enrich._consume_pending
# ---------------------------------------------------------------------------

def test_consume_pending_returns_none_when_missing(tmp_path):
    assert _consume_pending(tmp_path) is None


def test_consume_pending_archives_empty_marker(tmp_path):
    """Empty marker → no injection, but still archived."""
    pending = tmp_path / ".winkers_pending.md"
    pending.write_text(EMPTY_PENDING_MARKER, encoding="utf-8")
    assert _consume_pending(tmp_path) is None
    assert not pending.exists()
    assert any((tmp_path / ".winkers" / "history").glob("pending_*.md"))


def test_consume_pending_returns_real_content_and_archives(tmp_path):
    pending = tmp_path / ".winkers_pending.md"
    body = "- [ ] update X\n- [ ] sync Y"
    pending.write_text(body, encoding="utf-8")
    out = _consume_pending(tmp_path)
    assert out is not None
    assert "update X" in out
    assert "sync Y" in out
    assert not pending.exists()
    archives = list((tmp_path / ".winkers" / "history").glob("pending_*.md"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == body


def test_consume_pending_idempotent_after_archive(tmp_path):
    """After consumption, second call returns None (file is gone)."""
    (tmp_path / ".winkers_pending.md").write_text("- [ ] X", encoding="utf-8")
    assert _consume_pending(tmp_path) is not None
    assert _consume_pending(tmp_path) is None


def test_consume_pending_with_unparseable_content_still_archives(tmp_path):
    """Unparseable content (e.g. binary garbage) is still moved to history
    so it doesn't keep showing on every prompt."""
    pending = tmp_path / ".winkers_pending.md"
    pending.write_text("\x00binary\x01garbage", encoding="utf-8")
    _consume_pending(tmp_path)  # should not raise
    assert not pending.exists()
