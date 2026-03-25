"""Tests for Phase 3: improve viewer, insights prompt, redo detection."""


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
