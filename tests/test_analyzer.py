"""Tests for session analyzer and insights store."""

import json
from unittest.mock import MagicMock, patch

import pytest

from winkers.analyzer import (
    AnalysisResult,
    Insight,
    _build_user_message,
    _summarize_params,
)
from winkers.insights_store import (
    InsightsStore,
    StoredInsight,
    _find_similar,
    _similarity,
)
from winkers.models import (
    DebtDelta,
    ScoredSession,
    SessionRecord,
    ToolCall,
)


def _make_scored(
    session_id: str = "sess-001",
    task: str = "Add payment feature",
    total_turns: int = 10,
    exploration: int = 5,
    modification: int = 3,
    verification: int = 2,
    complexity_delta: int = 0,
    score: float = 0.7,
    tool_calls: list[ToolCall] | None = None,
) -> ScoredSession:
    session = SessionRecord(
        session_id=session_id,
        started_at="2026-03-25T10:00:00Z",
        completed_at="2026-03-25T10:30:00Z",
        model="claude-sonnet-4-6",
        task_prompt=task,
        task_hash="abc123",
        total_turns=total_turns,
        exploration_turns=exploration,
        modification_turns=modification,
        verification_turns=verification,
        tool_calls=tool_calls or [],
    )
    debt = DebtDelta(complexity_delta=complexity_delta)
    return ScoredSession(
        session=session, debt=debt, score=score,
    )


# ---------------------------------------------------------------------------
# Analyzer unit tests
# ---------------------------------------------------------------------------

class TestBuildUserMessage:
    def test_contains_task(self):
        scored = _make_scored(task="Fix login bug")
        msg = _build_user_message(scored, "{}")
        assert "Fix login bug" in msg

    def test_contains_debt_delta(self):
        scored = _make_scored(complexity_delta=15)
        msg = _build_user_message(scored, "{}")
        assert "complexity_delta: 15" in msg

    def test_contains_semantic_json(self):
        scored = _make_scored()
        sem = '{"constraints": [{"id": "C001"}]}'
        msg = _build_user_message(scored, sem)
        assert "C001" in msg

    def test_contains_tool_calls(self):
        scored = _make_scored(tool_calls=[
            ToolCall(name="Read", input_params={"file_path": "/a.py"}),
            ToolCall(name="Edit", input_params={"file_path": "/a.py"}),
        ])
        msg = _build_user_message(scored, "{}")
        assert "Read" in msg
        assert "Edit" in msg

    def test_contains_score(self):
        scored = _make_scored(score=0.42)
        msg = _build_user_message(scored, "{}")
        assert "0.42" in msg


class TestSummarizeParams:
    def test_empty(self):
        assert _summarize_params({}) == ""

    def test_short_params(self):
        result = _summarize_params({"file_path": "/src/a.py"})
        assert "file_path=/src/a.py" in result

    def test_long_params_truncated(self):
        result = _summarize_params({"content": "x" * 100})
        assert "..." in result
        assert len(result) < 100


def _mock_anthropic():
    mock_mod = MagicMock()
    mock_client = MagicMock()
    mock_mod.Anthropic.return_value = mock_client
    return mock_mod, mock_client


def _run_analyze(scored, response_text, **kwargs):
    """Helper: run analyze_session with mocked Anthropic API."""
    mock_mod, mock_client = _mock_anthropic()
    resp = MagicMock()
    resp.content = [MagicMock(text=response_text)]
    resp.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create.return_value = resp

    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        from winkers.analyzer import analyze_session as _analyze
        # Re-assign client after module reload picks up mock
        result = _analyze(scored, "{}", api_key="test-key")
    return result


class TestAnalyzeSession:
    def test_calls_api_and_parses_result(self):
        scored = _make_scored()
        response = json.dumps([{
            "category": "CONSTRAINT",
            "description": "Agent didn't know tax is per line item",
            "turns_affected": [3, 4],
            "turns_wasted": 2,
            "tokens_wasted": 3000,
            "semantic_target": "constraints",
            "injection_content": "Tax must be per line item",
            "priority": "high",
        }])
        result = _run_analyze(scored, response)

        assert len(result.insights) == 1
        assert result.insights[0].category == "CONSTRAINT"
        assert result.insights[0].injection_content == "Tax must be per line item"
        assert result.session_id == "sess-001"

    def test_forces_debt_insight_on_high_complexity(self):
        scored = _make_scored(complexity_delta=15)
        result = _run_analyze(scored, "[]")

        assert len(result.insights) == 1
        assert result.insights[0].category == "DEBT"
        assert result.insights[0].priority == "high"

    def test_low_score_escalates_debt_priority(self):
        scored = _make_scored(complexity_delta=15, score=0.3)
        response = json.dumps([{
            "category": "DEBT",
            "description": "Inlined too much",
            "turns_wasted": 0,
            "semantic_target": "conventions",
            "injection_content": "Split large functions",
            "priority": "medium",
        }])
        result = _run_analyze(scored, response)

        debt_insights = [i for i in result.insights if i.category == "DEBT"]
        assert all(i.priority == "high" for i in debt_insights)


# ---------------------------------------------------------------------------
# Insights store tests
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical(self):
        assert _similarity("hello world", "hello world") == pytest.approx(1.0)

    def test_similar(self):
        assert _similarity(
            "tax must be per line item",
            "tax should be per line item",
        ) > 0.8

    def test_different(self):
        assert _similarity("hello", "goodbye world") < 0.5

    def test_empty(self):
        assert _similarity("", "hello") == 0.0


class TestFindSimilar:
    def test_finds_match(self):
        existing = [
            StoredInsight(
                category="CONSTRAINT",
                description="tax must be per line item",
                semantic_target="constraints",
                injection_content="Tax is per line item",
                session_ids=["s1"],
            ),
        ]
        new = Insight(
            category="CONSTRAINT",
            description="tax should be per line item",
            semantic_target="constraints",
            injection_content="Tax is per line item",
        )
        assert _find_similar(new, existing) is existing[0]

    def test_no_match_different_target(self):
        existing = [
            StoredInsight(
                category="CONSTRAINT",
                description="tax must be per line item",
                semantic_target="constraints",
                injection_content="Tax is per line item",
                session_ids=["s1"],
            ),
        ]
        new = Insight(
            category="CONVENTION",
            description="tax must be per line item",
            semantic_target="conventions",
            injection_content="Tax is per line item",
        )
        assert _find_similar(new, existing) is None

    def test_skips_fixed(self):
        existing = [
            StoredInsight(
                category="CONSTRAINT",
                description="tax must be per line item",
                semantic_target="constraints",
                injection_content="Tax is per line item",
                session_ids=["s1"],
                status="fixed",
            ),
        ]
        new = Insight(
            category="CONSTRAINT",
            description="tax must be per line item",
            semantic_target="constraints",
            injection_content="Tax is per line item",
        )
        assert _find_similar(new, existing) is None


class TestInsightsStore:
    def test_merge_new(self, tmp_path):
        store = InsightsStore(tmp_path)
        result = AnalysisResult(
            session_id="s1",
            insights=[
                Insight(
                    category="CONSTRAINT",
                    description="tax is per line",
                    semantic_target="constraints",
                    injection_content="Tax per line item",
                    priority="low",
                    session_id="s1",
                ),
            ],
        )
        merged = store.merge(result)
        assert len(merged) == 1
        assert merged[0].occurrences == 1
        assert merged[0].session_ids == ["s1"]

    def test_merge_duplicate_increments(self, tmp_path):
        store = InsightsStore(tmp_path)

        result1 = AnalysisResult(
            session_id="s1",
            insights=[
                Insight(
                    category="CONSTRAINT",
                    description="tax is per line item",
                    semantic_target="constraints",
                    injection_content="Tax per line item",
                    session_id="s1",
                ),
            ],
        )
        store.merge(result1)

        result2 = AnalysisResult(
            session_id="s2",
            insights=[
                Insight(
                    category="CONSTRAINT",
                    description="tax should be per line item",
                    semantic_target="constraints",
                    injection_content="Tax per line item",
                    session_id="s2",
                ),
            ],
        )
        merged = store.merge(result2)
        assert len(merged) == 1
        assert merged[0].occurrences == 2
        assert merged[0].priority == "medium"
        assert "s1" in merged[0].session_ids
        assert "s2" in merged[0].session_ids

    def test_merge_three_times_high_priority(self, tmp_path):
        store = InsightsStore(tmp_path)
        for i in range(3):
            result = AnalysisResult(
                session_id=f"s{i}",
                insights=[
                    Insight(
                        category="CONVENTION",
                        description="new operation needs new file",
                        semantic_target="conventions",
                        injection_content="One service = one file",
                        session_id=f"s{i}",
                    ),
                ],
            )
            store.merge(result)

        items = store.open_insights()
        assert len(items) == 1
        assert items[0].occurrences == 3
        assert items[0].priority == "high"

    def test_open_insights_excludes_fixed(self, tmp_path):
        store = InsightsStore(tmp_path)
        result = AnalysisResult(
            session_id="s1",
            insights=[
                Insight(
                    category="CONSTRAINT",
                    description="rule A",
                    semantic_target="constraints",
                    injection_content="Rule A text",
                    session_id="s1",
                ),
                Insight(
                    category="CONVENTION",
                    description="rule B",
                    semantic_target="conventions",
                    injection_content="Rule B text",
                    session_id="s1",
                ),
            ],
        )
        store.merge(result)
        store.mark_fixed([0])

        open_items = store.open_insights()
        assert len(open_items) == 1

    def test_save_load_roundtrip(self, tmp_path):
        store = InsightsStore(tmp_path)
        result = AnalysisResult(
            session_id="s1",
            insights=[
                Insight(
                    category="DEBT",
                    description="complexity grew",
                    semantic_target="conventions",
                    injection_content="Keep functions small",
                    priority="high",
                    session_id="s1",
                    turns_wasted=5,
                    tokens_wasted=8000,
                ),
            ],
        )
        store.merge(result)

        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].category == "DEBT"
        assert loaded[0].turns_wasted == 5
        assert loaded[0].injection_content == "Keep functions small"
