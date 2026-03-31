"""Tests for Phase 3: improve viewer, insights prompt, redo detection, CLI commands."""

from unittest.mock import patch

from winkers.insights_store import InsightsStore, StoredInsight
from winkers.semantic import build_insights_prompt

# ---------------------------------------------------------------------------
# build_insights_prompt
# ---------------------------------------------------------------------------

class TestBuildInsightsPrompt:
    def test_empty_when_no_insights(self, tmp_path):
        result = build_insights_prompt(tmp_path)
        assert result == ""

    def test_includes_high_priority(self, tmp_path):
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONSTRAINT",
                description="tax per line item",
                semantic_target="constraints",
                injection_content="Tax must be calculated per line item.",
                priority="high",
                occurrences=1,
                session_ids=["s1"],
            ),
        ])

        result = build_insights_prompt(tmp_path)
        assert "Tax must be calculated per line item." in result
        assert "constraints" in result

    def test_excludes_low_priority_single_occurrence(self, tmp_path):
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONVENTION",
                description="minor thing",
                semantic_target="conventions",
                injection_content="Some low priority rule.",
                priority="low",
                occurrences=1,
                session_ids=["s1"],
            ),
        ])

        result = build_insights_prompt(tmp_path)
        assert result == ""

    def test_includes_medium_with_2_occurrences(self, tmp_path):
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONVENTION",
                description="new op new file",
                semantic_target="conventions",
                injection_content="New operation = new service file.",
                priority="medium",
                occurrences=2,
                session_ids=["s1", "s2"],
            ),
        ])

        result = build_insights_prompt(tmp_path)
        assert "New operation = new service file." in result
        assert "seen 2x" in result

    def test_excludes_medium_with_1_occurrence(self, tmp_path):
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONVENTION",
                description="new op new file",
                semantic_target="conventions",
                injection_content="New operation = new service file.",
                priority="medium",
                occurrences=1,
                session_ids=["s1"],
            ),
        ])

        result = build_insights_prompt(tmp_path)
        assert result == ""

    def test_groups_by_target(self, tmp_path):
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONSTRAINT",
                description="rule A",
                semantic_target="constraints",
                injection_content="Constraint A.",
                priority="high",
                session_ids=["s1"],
            ),
            StoredInsight(
                category="CONVENTION",
                description="rule B",
                semantic_target="conventions",
                injection_content="Convention B.",
                priority="high",
                session_ids=["s1"],
            ),
        ])

        result = build_insights_prompt(tmp_path)
        assert "### constraints" in result
        assert "### conventions" in result

    def test_excludes_fixed_insights(self, tmp_path):
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONSTRAINT",
                description="fixed rule",
                semantic_target="constraints",
                injection_content="Already applied.",
                priority="high",
                session_ids=["s1"],
                status="fixed",
            ),
        ])

        result = build_insights_prompt(tmp_path)
        assert result == ""


# ---------------------------------------------------------------------------
# Redo detection
# ---------------------------------------------------------------------------

class TestRedoDetection:
    def test_redo_warning_created(self, tmp_path):
        from winkers.models import DebtDelta, ScoredSession, SessionRecord
        from winkers.session_store import SessionStore

        store = SessionStore(tmp_path)

        # First session: rejected
        s1 = ScoredSession(
            session=SessionRecord(
                session_id="s1",
                started_at="2026-03-25T10:00:00Z",
                completed_at="2026-03-25T10:30:00Z",
                task_prompt="Add late fees",
                task_hash="latefees",
                user_corrections=["no, don't inline that"],
            ),
            debt=DebtDelta(complexity_delta=15),
            score=0.3,
        )
        store.save(s1)

        # Second session: same task
        s2 = ScoredSession(
            session=SessionRecord(
                session_id="s2",
                started_at="2026-03-25T11:00:00Z",
                completed_at="2026-03-25T11:30:00Z",
                task_prompt="Add late fees",
                task_hash="latefees",
            ),
            score=0.5,
        )
        store.save(s2)

        # Simulate _check_redo
        from winkers.cli.main import _check_redo
        _check_redo(tmp_path, store, s2)

        redo_path = tmp_path / ".winkers" / "redo_warning.md"
        assert redo_path.exists()
        content = redo_path.read_text(encoding="utf-8")
        assert "Add late fees" in content
        assert "complexity grew by 15" in content

    def test_redo_warning_cleared_on_success(self, tmp_path):
        from winkers.models import ScoredSession, SessionRecord
        from winkers.session_store import SessionStore

        store = SessionStore(tmp_path)
        redo_path = tmp_path / ".winkers" / "redo_warning.md"
        redo_path.parent.mkdir(parents=True, exist_ok=True)
        redo_path.write_text("old warning", encoding="utf-8")

        scored = ScoredSession(
            session=SessionRecord(
                session_id="s1",
                started_at="2026-03-25T10:00:00Z",
                completed_at="2026-03-25T10:30:00Z",
                task_hash="abc",
            ),
            score=0.85,
        )

        from winkers.cli.main import _check_redo
        _check_redo(tmp_path, store, scored)

        assert not redo_path.exists()

    def test_no_redo_warning_without_rejected_history(self, tmp_path):
        from winkers.models import ScoredSession, SessionRecord
        from winkers.session_store import SessionStore

        store = SessionStore(tmp_path)

        scored = ScoredSession(
            session=SessionRecord(
                session_id="s1",
                started_at="2026-03-25T10:00:00Z",
                completed_at="2026-03-25T10:30:00Z",
                task_hash="newtask",
            ),
            score=0.5,
        )

        from winkers.cli.main import _check_redo
        _check_redo(tmp_path, store, scored)

        redo_path = tmp_path / ".winkers" / "redo_warning.md"
        assert not redo_path.exists()


# ---------------------------------------------------------------------------
# improve --apply
# ---------------------------------------------------------------------------

class TestImproveApply:
    def test_apply_injects_constraints(self, tmp_path):
        """improve --apply adds high-priority injection_content to semantic.json."""
        from winkers.insights_store import InsightsStore, StoredInsight
        from winkers.semantic import SemanticLayer, SemanticStore

        # Setup semantic.json
        sem = SemanticLayer(data_flow="A -> B", constraints=["existing rule"])
        SemanticStore(tmp_path).save(sem)

        # Setup insights
        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="CONSTRAINT",
                description="tax per line",
                semantic_target="constraints",
                injection_content="Tax must be per line item.",
                priority="high",
                session_ids=["s1"],
                occurrences=3,
            ),
            StoredInsight(
                category="CONVENTION",
                description="minor thing",
                semantic_target="conventions",
                injection_content="Some low priority rule.",
                priority="low",
                session_ids=["s1"],
            ),
        ])

        from click.testing import CliRunner

        from winkers.cli.main import cli
        result = CliRunner().invoke(cli, ["improve", str(tmp_path), "--apply"])
        assert result.exit_code == 0, result.output
        assert "Applied 1" in result.output

        # Verify semantic.json updated
        updated = SemanticStore(tmp_path).load()
        assert "Tax must be per line item." in updated.constraints
        assert "existing rule" in updated.constraints

        # Verify insight marked as fixed
        all_insights = InsightsStore(tmp_path).load()
        high_items = [i for i in all_insights if i.priority == "high"]
        assert all(i.status == "fixed" for i in high_items)

    def test_dry_run_shows_insights(self, tmp_path):
        """Default improve (no --apply) just displays insights."""
        from winkers.insights_store import InsightsStore, StoredInsight

        store = InsightsStore(tmp_path)
        store.save([
            StoredInsight(
                category="DEBT",
                description="complexity grew",
                semantic_target="conventions",
                injection_content="Keep complexity stable.",
                priority="high",
                session_ids=["s1"],
            ),
        ])

        from click.testing import CliRunner

        from winkers.cli.main import cli
        result = CliRunner().invoke(cli, ["improve", str(tmp_path)])
        assert result.exit_code == 0
        assert "complexity grew" in result.output
        assert "Run with --apply" in result.output


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------

class TestAnalyzeCommand:
    def test_analyze_no_sessions(self, tmp_path):
        from click.testing import CliRunner

        from winkers.cli.main import cli

        result = CliRunner(env={"ANTHROPIC_API_KEY": "sk-test"}).invoke(
            cli, ["analyze", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "No recorded sessions" in result.output

    def test_analyze_runs_on_session(self, tmp_path):
        """analyze calls the API and merges insights."""
        from winkers.analyzer import AnalysisResult, Insight
        from winkers.models import ScoredSession, SessionRecord
        from winkers.semantic import SemanticLayer, SemanticStore
        from winkers.session_store import SessionStore

        # Setup session
        store = SessionStore(tmp_path)
        scored = ScoredSession(
            session=SessionRecord(
                session_id="s1",
                started_at="2026-03-25T10:00:00Z",
                completed_at="2026-03-25T10:30:00Z",
                task_prompt="Add late fees",
            ),
            score=0.6,
        )
        store.save(scored)

        # Setup semantic
        SemanticStore(tmp_path).save(SemanticLayer())

        mock_result = AnalysisResult(
            session_id="s1",
            insights=[
                Insight(
                    category="CONSTRAINT",
                    description="tax rule",
                    semantic_target="constraints",
                    injection_content="Tax per line.",
                    priority="high",
                    session_id="s1",
                ),
            ],
        )

        from click.testing import CliRunner

        from winkers.cli.main import cli

        with patch("winkers.analyzer.analyze_session", return_value=mock_result):
            result = CliRunner(env={"ANTHROPIC_API_KEY": "sk-test"}).invoke(
                cli, ["analyze", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        assert "1 insight(s)" in result.output

        # Verify insights saved
        insights = InsightsStore(tmp_path).open_insights()
        assert len(insights) == 1
        assert insights[0].injection_content == "Tax per line."
