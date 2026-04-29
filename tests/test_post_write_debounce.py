"""Tests for the post-write content-hash debounce cache."""

from __future__ import annotations

from pathlib import Path

from winkers.hooks._debounce import (
    file_hash,
    remember,
    should_skip,
)


class TestFileHash:
    def test_returns_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("hello", encoding="utf-8")
        h = file_hash(f)
        # SHA-256 of "hello"
        assert h == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert file_hash(tmp_path / "ghost.py") is None

    def test_changes_when_content_changes(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("a", encoding="utf-8")
        h1 = file_hash(f)
        f.write_text("b", encoding="utf-8")
        h2 = file_hash(f)
        assert h1 != h2


class TestSkipAndRemember:
    def test_initially_does_not_skip(self, tmp_path: Path) -> None:
        assert not should_skip(tmp_path, "sid", "app/foo.py", "abc123")

    def test_skip_after_remember(self, tmp_path: Path) -> None:
        remember(tmp_path, "sid", "app/foo.py", "abc123")
        assert should_skip(tmp_path, "sid", "app/foo.py", "abc123")

    def test_does_not_skip_on_different_hash(self, tmp_path: Path) -> None:
        remember(tmp_path, "sid", "app/foo.py", "abc123")
        assert not should_skip(tmp_path, "sid", "app/foo.py", "different")

    def test_does_not_skip_for_different_file(self, tmp_path: Path) -> None:
        remember(tmp_path, "sid", "app/foo.py", "abc123")
        assert not should_skip(tmp_path, "sid", "app/bar.py", "abc123")

    def test_isolated_per_session(self, tmp_path: Path) -> None:
        remember(tmp_path, "sid-A", "app/foo.py", "abc123")
        # Different session — cold cache, must not skip.
        assert not should_skip(tmp_path, "sid-B", "app/foo.py", "abc123")

    def test_remember_overwrites_existing_entry(self, tmp_path: Path) -> None:
        remember(tmp_path, "sid", "app/foo.py", "first")
        remember(tmp_path, "sid", "app/foo.py", "second")
        assert should_skip(tmp_path, "sid", "app/foo.py", "second")
        assert not should_skip(tmp_path, "sid", "app/foo.py", "first")

    def test_corrupted_cache_recovers(self, tmp_path: Path) -> None:
        from winkers.hooks._debounce import CACHE_FILENAME
        from winkers.session.session_dir import get_session_dir

        sess = get_session_dir(tmp_path, "sid")
        (sess / CACHE_FILENAME).write_text("not json", encoding="utf-8")
        # Should treat unreadable cache as empty rather than crash.
        assert not should_skip(tmp_path, "sid", "app/foo.py", "abc123")
        remember(tmp_path, "sid", "app/foo.py", "abc123")
        assert should_skip(tmp_path, "sid", "app/foo.py", "abc123")
