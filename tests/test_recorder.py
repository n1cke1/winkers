"""Tests for session recording: transcript parsing, scoring, session store."""

import json

import pytest

from winkers.models import (
    CommitBinding,
    DebtDelta,
    ScoredSession,
    SessionRecord,
)
from winkers.recorder import parse_transcript, parse_transcript_text
from winkers.scoring import compute_debt_delta, estimate_score
from winkers.session_store import SessionStore

# ---------------------------------------------------------------------------
# Transcript fixtures
# ---------------------------------------------------------------------------

def _make_transcript_lines(
    session_id: str = "test-session-001",
    task: str = "Add payment feature",
    model: str = "claude-sonnet-4-6",
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
) -> str:
    """Build a minimal transcript JSONL string."""
    lines = []

    # Queue operation
    lines.append(json.dumps({
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": "2026-03-25T10:00:00.000Z",
        "sessionId": session_id,
    }))

    # User message (task prompt)
    lines.append(json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": task}],
        },
        "uuid": "user-001",
        "timestamp": "2026-03-25T10:00:01.000Z",
        "sessionId": session_id,
        "version": "2.1.81",
        "cwd": "c:\\TestProject",
    }))

    # Default tool calls if not provided
    if tool_calls is None:
        tool_calls = [
            {"name": "Read", "input": {"file_path": "/src/payment.py"}},
            {"name": "Grep", "input": {"pattern": "calculate_total", "path": "/src/"}},
            {"name": "Edit", "input": {
                "file_path": "/src/payment.py",
                "old_string": "x", "new_string": "y",
            }},
            {"name": "Bash", "input": {"command": "pytest tests/ -v"}},
        ]
    if tool_results is None:
        tool_results = [
            {"content": "file content here", "is_error": False},
            {"content": "src/payment.py:10: calculate_total", "is_error": False},
            {"content": "ok", "is_error": False},
            {"content": "4 passed", "is_error": False},
        ]

    for i, (tc, tr) in enumerate(zip(tool_calls, tool_results)):
        tool_id = f"toolu_{i:03d}"

        # Assistant message with tool_use
        lines.append(json.dumps({
            "type": "assistant",
            "message": {
                "model": model,
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tool_id, "name": tc["name"], "input": tc["input"]},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20},
            },
            "uuid": f"assistant-{i:03d}",
            "timestamp": f"2026-03-25T10:0{i}:10.000Z",
            "sessionId": session_id,
        }))

        # Tool result
        stdout = tr.get("content", "")
        lines.append(json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"tool_use_id": tool_id, "type": "tool_result",
                     "content": stdout, "is_error": tr.get("is_error", False)},
                ],
            },
            "toolUseResult": {"stdout": stdout, "stderr": "", "interrupted": False},
            "uuid": f"result-{i:03d}",
            "timestamp": f"2026-03-25T10:0{i}:11.000Z",
            "sessionId": session_id,
        }))

    # Final assistant text (end_turn)
    lines.append(json.dumps({
        "type": "assistant",
        "message": {
            "model": model,
            "role": "assistant",
            "content": [{"type": "text", "text": "Done! Payment feature added."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 30},
        },
        "uuid": "assistant-final",
        "timestamp": "2026-03-25T10:05:00.000Z",
        "sessionId": session_id,
    }))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Recorder tests
# ---------------------------------------------------------------------------

class TestTranscriptParser:
    def test_basic_parse(self):
        text = _make_transcript_lines()
        record = parse_transcript_text(text)

        assert record.session_id == "test-session-001"
        assert record.model == "claude-sonnet-4-6"
        assert record.task_prompt == "Add payment feature"
        assert record.task_hash  # non-empty
        assert record.started_at.startswith("2026-03-25")
        assert record.completed_at.startswith("2026-03-25")

    def test_tool_call_count(self):
        text = _make_transcript_lines()
        record = parse_transcript_text(text)

        assert record.total_turns == 4
        assert len(record.tool_calls) == 4

    def test_turn_classification(self):
        text = _make_transcript_lines()
        record = parse_transcript_text(text)

        assert record.exploration_turns == 2  # Read + Grep
        assert record.modification_turns == 1  # Edit
        assert record.verification_turns == 1  # Bash(pytest)

    def test_file_tracking(self):
        text = _make_transcript_lines()
        record = parse_transcript_text(text)

        assert "/src/payment.py" in record.files_read
        assert "/src/payment.py" in record.files_modified

    def test_test_detection_pass(self):
        text = _make_transcript_lines()
        record = parse_transcript_text(text)

        assert record.tests_passed is True

    def test_test_detection_fail(self):
        text = _make_transcript_lines(
            tool_calls=[
                {"name": "Bash", "input": {"command": "pytest tests/"}},
            ],
            tool_results=[
                {"content": "2 passed, 1 failed", "is_error": False},
            ],
        )
        record = parse_transcript_text(text)
        assert record.tests_passed is False

    def test_session_end_agent_done(self):
        text = _make_transcript_lines()
        record = parse_transcript_text(text)
        assert record.session_end == "agent_done"

    def test_winkers_tool_tracking(self):
        text = _make_transcript_lines(
            tool_calls=[
                {"name": "mcp__winkers__map", "input": {}},
                {"name": "mcp__winkers__scope", "input": {"function": "foo"}},
                {"name": "mcp__winkers__scope", "input": {"function": "bar"}},
            ],
            tool_results=[
                {"content": "map output"},
                {"content": "scope output"},
                {"content": "scope output"},
            ],
        )
        record = parse_transcript_text(text)
        assert record.winkers_calls == {"map": 1, "scope": 2}

    def test_user_correction_detection(self):
        """User correction after first human message should be detected."""
        lines = []

        # First user message (task)
        lines.append(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "Add feature X"}]},
            "uuid": "u1", "timestamp": "2026-03-25T10:00:00Z", "sessionId": "s1",
        }))

        # Assistant does something
        lines.append(json.dumps({
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6", "role": "assistant",
                "content": [{"type": "text", "text": "I will do Y"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            "uuid": "a1", "timestamp": "2026-03-25T10:01:00Z", "sessionId": "s1",
        }))

        # User corrects
        lines.append(json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text",
                             "text": "No, don't do that. Instead use Z."}],
            },
            "uuid": "u2", "timestamp": "2026-03-25T10:02:00Z", "sessionId": "s1",
        }))

        record = parse_transcript_text("\n".join(lines))
        assert len(record.user_corrections) == 1
        assert "don't" in record.user_corrections[0]

    def test_parse_from_file(self, tmp_path):
        text = _make_transcript_lines()
        f = tmp_path / "transcript.jsonl"
        f.write_text(text, encoding="utf-8")

        record = parse_transcript(f)
        assert record.session_id == "test-session-001"
        assert record.total_turns == 4

    def test_empty_transcript(self):
        record = parse_transcript_text("")
        assert record.session_id == ""
        assert record.total_turns == 0

    def test_write_creates_file(self):
        text = _make_transcript_lines(
            tool_calls=[
                {"name": "Write", "input": {"file_path": "/src/new_module.py", "content": "pass"}},
            ],
            tool_results=[
                {"content": "File written"},
            ],
        )
        record = parse_transcript_text(text)
        assert "/src/new_module.py" in record.files_created


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

class TestApprovalScoring:
    def test_baseline_score(self):
        session = SessionRecord(
            session_id="s1", started_at="2026-03-25T10:00:00Z",
            completed_at="2026-03-25T10:30:00Z",
        )
        score = estimate_score(session, CommitBinding(), DebtDelta())
        # 0.5 base + 0.15 (complexity_delta=0) + 0.05 (import_edges_delta=0)
        assert score == pytest.approx(0.7, abs=0.01)

    def test_committed_and_tests_pass(self):
        session = SessionRecord(
            session_id="s1", started_at="", completed_at="",
            tests_passed=True,
        )
        commit = CommitBinding(status="committed")
        debt = DebtDelta(complexity_delta=-2)
        score = estimate_score(session, commit, debt)
        # 0.5 + 0.2 (committed) + 0.15 (tests) + 0.15 (complexity down) + 0.05 (no coupling)
        assert score > 0.8

    def test_reverted_and_debt_high(self):
        session = SessionRecord(
            session_id="s1", started_at="", completed_at="",
            tests_passed=False, session_end="user_killed",
            user_corrections=["no", "wrong"],
        )
        commit = CommitBinding(status="reverted")
        debt = DebtDelta(complexity_delta=25, max_function_lines=120)
        score = estimate_score(session, commit, debt)
        assert score < 0.2

    def test_modular_bonus(self):
        session = SessionRecord(
            session_id="s1", started_at="", completed_at="",
        )
        debt_modular = DebtDelta(files_created=2, complexity_delta=3)
        debt_inline = DebtDelta(files_created=0, complexity_delta=3)
        score_modular = estimate_score(session, CommitBinding(), debt_modular)
        score_inline = estimate_score(session, CommitBinding(), debt_inline)
        assert score_modular > score_inline

    def test_score_clamped(self):
        session = SessionRecord(
            session_id="s1", started_at="", completed_at="",
            tests_passed=True,
        )
        commit = CommitBinding(status="committed")
        debt = DebtDelta(complexity_delta=-10, files_created=3)
        score = estimate_score(session, commit, debt)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Debt delta tests
# ---------------------------------------------------------------------------

class TestDebtDelta:
    def test_no_graphs(self):
        session = SessionRecord(
            session_id="s1", started_at="", completed_at="",
            files_created=["a.py", "b.py"], files_modified=["c.py"],
        )
        delta = compute_debt_delta(session, None, None)
        assert delta.files_created == 2
        assert delta.files_modified == 1


# ---------------------------------------------------------------------------
# Session store tests
# ---------------------------------------------------------------------------

class TestSessionStore:
    def test_save_and_load(self, tmp_path):
        store = SessionStore(tmp_path)
        session = SessionRecord(
            session_id="sess-abc",
            started_at="2026-03-25T10:00:00Z",
            completed_at="2026-03-25T10:30:00Z",
            task_prompt="test task",
            task_hash="abc12345",
            total_turns=5,
        )
        scored = ScoredSession(session=session, score=0.75)
        path = store.save(scored)

        assert path.exists()
        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0].session.session_id == "sess-abc"
        assert loaded[0].score == pytest.approx(0.75)

    def test_recorded_session_ids(self, tmp_path):
        store = SessionStore(tmp_path)
        session = SessionRecord(
            session_id="sess-xyz",
            started_at="2026-03-25T10:00:00Z",
            completed_at="2026-03-25T10:30:00Z",
            task_hash="xyz12345",
        )
        store.save(ScoredSession(session=session))

        ids = store.recorded_session_ids()
        assert "sess-xyz" in ids

    def test_no_duplicate_filename(self, tmp_path):
        store = SessionStore(tmp_path)
        session = SessionRecord(
            session_id="sess-1",
            started_at="2026-03-25T10:00:00Z",
            completed_at="2026-03-25T10:30:00Z",
            task_hash="same1234",
        )
        path1 = store.save(ScoredSession(session=session))

        session2 = SessionRecord(
            session_id="sess-2",
            started_at="2026-03-25T10:00:00Z",
            completed_at="2026-03-25T10:30:00Z",
            task_hash="same1234",
        )
        path2 = store.save(ScoredSession(session=session2))

        assert path1 != path2
        assert len(store.load_all()) == 2

    def test_find_by_task_hash(self, tmp_path):
        store = SessionStore(tmp_path)
        for i, th in enumerate(["aaa", "aaa", "bbb"]):
            session = SessionRecord(
                session_id=f"s-{i}",
                started_at="2026-03-25T10:00:00Z",
                completed_at="2026-03-25T10:30:00Z",
                task_hash=th,
            )
            store.save(ScoredSession(session=session))

        matches = store.find_by_task_hash("aaa")
        assert len(matches) == 2
