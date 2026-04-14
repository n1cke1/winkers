"""Tests for session_done MCP tool — PASS/FAIL audit + anti-loop."""

from pathlib import Path

import pytest

from winkers.conventions import ConventionRule, RulesFile, RulesStore
from winkers.graph import GraphBuilder
from winkers.mcp.tools import (
    _session_status,
    _tool_orient,
    _tool_session_done,
)
from winkers.resolver import CrossFileResolver
from winkers.session.state import SessionState, SessionStore, Warning, WriteEvent

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# PASS scenarios
# ---------------------------------------------------------------------------


class TestSessionDonePass:
    def test_pass_no_warnings(self, graph, tmp_path):
        """Clean session with no warnings → PASS."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_write(WriteEvent(
            timestamp="t1", file_path="modules/pricing.py",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "PASS"
        assert "issues" not in result

    def test_pass_only_resolved_warnings(self, graph, tmp_path):
        """All warnings resolved → PASS."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="calc.py::add", detail="sig changed",
            resolved=True,
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "PASS"

    def test_pass_empty_session(self, graph, tmp_path):
        """No writes, no warnings → PASS (nothing happened)."""
        (tmp_path / ".winkers").mkdir()

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "PASS"


# ---------------------------------------------------------------------------
# FAIL scenarios
# ---------------------------------------------------------------------------


class TestSessionDoneFail:
    def test_fail_broken_callers(self, graph, tmp_path):
        """Unresolved broken caller → FAIL."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        # Use a real function ID from the fixture
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="modules/pricing.py::calculate_price",
            detail="calculate_price() signature changed. 2 callers affected.",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "FAIL"
        assert len(result["issues"]) >= 1
        issue = result["issues"][0]
        assert issue["kind"] == "broken_caller"
        assert "hint" in result

    def test_fail_coherence_sync_not_modified(self, graph, tmp_path):
        """Coherence sync rule with unmodified sync_with files → FAIL."""
        (tmp_path / ".winkers").mkdir()

        # Create a coherence rule
        rules_file = RulesFile(rules=[
            ConventionRule(
                id=42,
                category="coherence",
                title="README synced with pipeline",
                content="Update README when pipeline changes",
                wrong_approach="Changing pipeline without updating docs",
                affects=["src/pipeline.py"],
                sync_with=["README.md"],
                fix_approach="sync",
                source="manual",
                created="2026-01-01",
            ),
        ])
        RulesStore(tmp_path).save(rules_file)

        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        # Modified pipeline but not README
        state.add_write(WriteEvent(
            timestamp="t1", file_path="src/pipeline.py",
        ))
        state.add_warning(Warning(
            kind="coherence", severity="warning",
            target="src/pipeline.py",
            detail='Rule #42 "README synced with pipeline": check README.md',
            fix_approach="sync",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "FAIL"
        assert any(i["kind"] == "coherence_sync" for i in result["issues"])
        sync_issue = next(i for i in result["issues"] if i["kind"] == "coherence_sync")
        assert "README.md" in sync_issue["unmodified_files"]

    def test_pass_coherence_sync_when_modified(self, graph, tmp_path):
        """Coherence sync rule with modified sync_with file → PASS."""
        (tmp_path / ".winkers").mkdir()

        rules_file = RulesFile(rules=[
            ConventionRule(
                id=42,
                category="coherence",
                title="README synced with pipeline",
                content="Update README when pipeline changes",
                affects=["src/pipeline.py"],
                sync_with=["README.md"],
                fix_approach="sync",
                source="manual",
                created="2026-01-01",
            ),
        ])
        RulesStore(tmp_path).save(rules_file)

        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        # Both pipeline AND README were modified
        state.add_write(WriteEvent(timestamp="t1", file_path="src/pipeline.py"))
        state.add_write(WriteEvent(timestamp="t2", file_path="README.md"))
        state.add_warning(Warning(
            kind="coherence", severity="warning",
            target="src/pipeline.py",
            detail='Rule #42 "README synced with pipeline": check README.md',
            fix_approach="sync",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "PASS"

    def test_coherence_derived_is_recommendation_not_fail(self, graph, tmp_path):
        """Derived coherence rules don't block PASS, become recommendations."""
        (tmp_path / ".winkers").mkdir()

        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="coherence", severity="warning",
            target="engine/equations.py",
            detail='Rule #14 "Variable count derived from source": check templates',
            fix_approach="derived",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "PASS"
        assert "recommendations" in result
        assert len(result["recommendations"]) >= 1
        assert result["recommendations"][0]["kind"] == "coherence_derived"

    def test_coherence_refactor_is_recommendation(self, graph, tmp_path):
        """Refactor coherence rules don't block PASS."""
        (tmp_path / ".winkers").mkdir()

        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="coherence", severity="warning",
            target="config.py",
            detail='Rule #15 "Timeout in one place": refactor needed',
            fix_approach="refactor",
        ))
        store.save(state)

        result = _tool_session_done(graph, tmp_path)
        assert result["status"] == "PASS"
        assert "recommendations" in result
        assert result["recommendations"][0]["kind"] == "coherence_refactor"


# ---------------------------------------------------------------------------
# Anti-loop
# ---------------------------------------------------------------------------


class TestSessionDoneAntiLoop:
    def test_second_call_always_pass(self, graph, tmp_path):
        """Second call → PASS even if issues remain (anti-loop)."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="modules/pricing.py::calculate_price",
            detail="calculate_price() sig changed. 2 callers.",
        ))
        store.save(state)

        # First call → FAIL
        result1 = _tool_session_done(graph, tmp_path)
        assert result1["status"] == "FAIL"

        # Second call → PASS (anti-loop)
        result2 = _tool_session_done(graph, tmp_path)
        assert result2["status"] == "PASS"
        assert "remaining_warnings" in result2

    def test_third_call_still_pass(self, graph, tmp_path):
        """Third+ calls also PASS."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="x::y", detail="issue",
        ))
        store.save(state)

        _tool_session_done(graph, tmp_path)  # 1st → FAIL
        _tool_session_done(graph, tmp_path)  # 2nd → PASS
        result3 = _tool_session_done(graph, tmp_path)  # 3rd → PASS
        assert result3["status"] == "PASS"

    def test_session_done_calls_counter(self, graph, tmp_path):
        """session_done_calls increments correctly."""
        (tmp_path / ".winkers").mkdir()

        _tool_session_done(graph, tmp_path)
        store = SessionStore(tmp_path)
        state = store.load()
        assert state is not None
        assert state.session_done_calls == 1

        _tool_session_done(graph, tmp_path)
        state = store.load()
        assert state.session_done_calls == 2


# ---------------------------------------------------------------------------
# orient() session status
# ---------------------------------------------------------------------------


class TestOrientSessionStatus:
    def test_orient_shows_session_when_active(self, graph, tmp_path):
        """orient() includes session status when session.json exists."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_write(WriteEvent(timestamp="t1", file_path="a.py"))
        state.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="x::y", detail="sig changed",
        ))
        store.save(state)

        result = _tool_orient(graph, {"include": ["map"]}, tmp_path)
        assert "session" in result
        assert result["session"]["writes"] == 1
        assert result["session"]["warnings"] == 1
        assert result["session"]["warnings_pending"] == 1

    def test_orient_no_session_when_inactive(self, graph, tmp_path):
        """orient() omits session key when no session.json."""
        result = _tool_orient(graph, {"include": ["map"]}, tmp_path)
        assert "session" not in result

    def test_session_status_helper(self, tmp_path):
        """_session_status returns None when no session."""
        assert _session_status(tmp_path) is None

    def test_session_status_with_pending(self, tmp_path):
        """_session_status returns pending warnings."""
        (tmp_path / ".winkers").mkdir()
        store = SessionStore(tmp_path)
        state = SessionState(started_at="2026-01-01T00:00:00Z")
        state.add_warning(Warning(
            kind="coherence", severity="warning",
            target="a.py", detail="check README.md",
        ))
        store.save(state)

        info = _session_status(tmp_path)
        assert info is not None
        assert info["warnings_pending"] == 1
        assert "check README.md" in info["pending"][0]
