"""Tests for ProjectStore — Wave 4a project.json merge + migration."""

from __future__ import annotations

import json
from pathlib import Path

from winkers.conventions import ConventionRule, RulesFile, RulesStore
from winkers.project import PROJECT_FILE, ProjectFile, ProjectStore
from winkers.semantic import SemanticLayer, SemanticStore, ZoneIntent
from winkers.store import STORE_DIR

# ---------------------------------------------------------------------------
# ProjectStore — round-trip + defaults
# ---------------------------------------------------------------------------


class TestProjectStoreRoundtrip:
    def test_load_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert ProjectStore(tmp_path).load() is None

    def test_save_then_load(self, tmp_path: Path) -> None:
        store = ProjectStore(tmp_path)
        bundle = ProjectFile(
            semantic=SemanticLayer(data_flow="A → B → C"),
            rules=RulesFile(project="test"),
        )
        store.save(bundle)
        loaded = store.load()
        assert loaded is not None
        assert loaded.semantic.data_flow == "A → B → C"
        assert loaded.rules.project == "test"

    def test_save_creates_file_at_expected_path(self, tmp_path: Path) -> None:
        store = ProjectStore(tmp_path)
        store.save(ProjectFile())
        assert (tmp_path / STORE_DIR / PROJECT_FILE).exists()

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / STORE_DIR / PROJECT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        assert ProjectStore(tmp_path).load() is None

    def test_load_or_default_returns_empty_on_clean_repo(self, tmp_path: Path) -> None:
        bundle = ProjectStore(tmp_path).load_or_default()
        # No legacy, no project → default-constructed bundle
        assert bundle.semantic.data_flow == ""
        assert bundle.rules.rules == []
        # Should NOT write project.json on a clean repo
        assert not (tmp_path / STORE_DIR / PROJECT_FILE).exists()


# ---------------------------------------------------------------------------
# Migration from legacy files
# ---------------------------------------------------------------------------


class TestProjectStoreMigration:
    def _seed_legacy_semantic(self, tmp_path: Path) -> None:
        path = tmp_path / STORE_DIR / "semantic.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        layer = SemanticLayer(
            data_flow="legacy data flow",
            domain_context="legacy domain",
            zone_intents={"app.py": ZoneIntent(why="x", wrong_approach="y")},
        )
        path.write_text(layer.model_dump_json(), encoding="utf-8")

    def _seed_legacy_rules(self, tmp_path: Path) -> None:
        path = tmp_path / STORE_DIR / "rules" / "rules.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        rules_file = RulesFile(
            project="legacy project",
            rules=[
                ConventionRule(
                    id=1, category="style", title="Test rule",
                    content="Some content",
                    source="manual", created="2026-01-01",
                ),
            ],
        )
        path.write_text(rules_file.model_dump_json(), encoding="utf-8")

    def test_migrates_both_legacy_files(self, tmp_path: Path) -> None:
        self._seed_legacy_semantic(tmp_path)
        self._seed_legacy_rules(tmp_path)

        bundle = ProjectStore(tmp_path).load_or_default()
        assert bundle.semantic.data_flow == "legacy data flow"
        assert bundle.semantic.zone_intents["app.py"].why == "x"
        assert bundle.rules.project == "legacy project"
        assert bundle.rules.rules[0].title == "Test rule"

        # Migration should write project.json so subsequent loads are direct.
        assert (tmp_path / STORE_DIR / PROJECT_FILE).exists()

    def test_migrates_only_semantic_when_rules_missing(
        self, tmp_path: Path,
    ) -> None:
        self._seed_legacy_semantic(tmp_path)
        bundle = ProjectStore(tmp_path).load_or_default()
        assert bundle.semantic.data_flow == "legacy data flow"
        # Rules section stays at default
        assert bundle.rules.rules == []

    def test_migrates_only_rules_when_semantic_missing(
        self, tmp_path: Path,
    ) -> None:
        self._seed_legacy_rules(tmp_path)
        bundle = ProjectStore(tmp_path).load_or_default()
        assert bundle.semantic.data_flow == ""
        assert bundle.rules.rules[0].title == "Test rule"

    def test_skips_unparseable_legacy_files(self, tmp_path: Path) -> None:
        path = tmp_path / STORE_DIR / "semantic.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        bundle = ProjectStore(tmp_path).load_or_default()
        # Treated as no-state — defaults
        assert bundle.semantic.data_flow == ""
        # And no project.json should be written either
        assert not (tmp_path / STORE_DIR / PROJECT_FILE).exists()

    def test_existing_project_takes_precedence_over_legacy(
        self, tmp_path: Path,
    ) -> None:
        # Both legacy and new files present — new wins.
        self._seed_legacy_semantic(tmp_path)
        ProjectStore(tmp_path).save(
            ProjectFile(semantic=SemanticLayer(data_flow="modern flow")),
        )
        bundle = ProjectStore(tmp_path).load_or_default()
        assert bundle.semantic.data_flow == "modern flow"


# ---------------------------------------------------------------------------
# Shim parity — SemanticStore + RulesStore continue to work
# ---------------------------------------------------------------------------


class TestSemanticStoreShim:
    def test_load_returns_none_on_clean_repo(self, tmp_path: Path) -> None:
        assert SemanticStore(tmp_path).load() is None

    def test_save_then_load(self, tmp_path: Path) -> None:
        store = SemanticStore(tmp_path)
        layer = SemanticLayer(data_flow="A → B")
        store.save(layer)
        loaded = store.load()
        assert loaded is not None
        assert loaded.data_flow == "A → B"

    def test_migrates_legacy_semantic_on_load(self, tmp_path: Path) -> None:
        path = tmp_path / STORE_DIR / "semantic.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        layer = SemanticLayer(data_flow="legacy")
        path.write_text(layer.model_dump_json(), encoding="utf-8")
        loaded = SemanticStore(tmp_path).load()
        assert loaded is not None
        assert loaded.data_flow == "legacy"

    def test_save_writes_to_project_json(self, tmp_path: Path) -> None:
        store = SemanticStore(tmp_path)
        store.save(SemanticLayer(data_flow="written"))
        # project.json should exist; legacy semantic.json should NOT.
        assert (tmp_path / STORE_DIR / PROJECT_FILE).exists()
        assert not (tmp_path / STORE_DIR / "semantic.json").exists()
        # The data lands in the right section.
        data = json.loads((tmp_path / STORE_DIR / PROJECT_FILE).read_text())
        assert data["semantic"]["data_flow"] == "written"


class TestRulesStoreShim:
    def _rule(self, **kw):
        return ConventionRule(
            id=kw.get("id", 1),
            category=kw.get("category", "style"),
            title=kw.get("title", "T"),
            content=kw.get("content", "C"),
            source=kw.get("source", "manual"),
            created=kw.get("created", "2026-01-01"),
        )

    def test_clean_repo_returns_default_rules_file(self, tmp_path: Path) -> None:
        rf = RulesStore(tmp_path).load()
        assert rf.rules == []

    def test_add_rule_persists(self, tmp_path: Path) -> None:
        store = RulesStore(tmp_path)
        store.add_rule(self._rule(id=1, title="A"))
        store.add_rule(self._rule(id=2, title="B"))
        loaded = store.load()
        assert [r.title for r in loaded.rules] == ["A", "B"]

    def test_delete_rule(self, tmp_path: Path) -> None:
        store = RulesStore(tmp_path)
        store.add_rule(self._rule(id=1, title="A"))
        store.add_rule(self._rule(id=2, title="B"))
        assert store.delete_rule(1) is True
        assert [r.title for r in store.load().rules] == ["B"]
        assert store.delete_rule(99) is False

    def test_exists_true_after_save(self, tmp_path: Path) -> None:
        store = RulesStore(tmp_path)
        assert store.exists() is False
        store.add_rule(self._rule())
        assert store.exists() is True

    def test_exists_true_with_legacy_only(self, tmp_path: Path) -> None:
        # Legacy on disk, no project.json
        path = tmp_path / STORE_DIR / "rules" / "rules.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        rules_file = RulesFile(rules=[self._rule()])
        path.write_text(rules_file.model_dump_json(), encoding="utf-8")
        assert RulesStore(tmp_path).exists() is True

    def test_migrates_legacy_rules_on_load(self, tmp_path: Path) -> None:
        path = tmp_path / STORE_DIR / "rules" / "rules.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        rules_file = RulesFile(
            rules=[self._rule(id=1, title="legacy rule")],
        )
        path.write_text(rules_file.model_dump_json(), encoding="utf-8")
        loaded = RulesStore(tmp_path).load()
        assert [r.title for r in loaded.rules] == ["legacy rule"]


# ---------------------------------------------------------------------------
# Cross-store parity: SemanticStore + RulesStore writes hit the same file
# ---------------------------------------------------------------------------


class TestCrossStore:
    def test_writes_share_one_project_json(self, tmp_path: Path) -> None:
        SemanticStore(tmp_path).save(SemanticLayer(data_flow="flow!"))
        rules = RulesFile(
            rules=[
                ConventionRule(
                    id=1, category="style", title="R", content="x",
                    source="manual", created="2026-01-01",
                )
            ],
        )
        RulesStore(tmp_path).save(rules)

        # One file holds both sections.
        data = json.loads((tmp_path / STORE_DIR / PROJECT_FILE).read_text())
        assert data["semantic"]["data_flow"] == "flow!"
        assert data["rules"]["rules"][0]["title"] == "R"

    def test_legacy_files_left_alone_after_migration(
        self, tmp_path: Path,
    ) -> None:
        """We don't delete legacy files on migration — preserves rollback."""
        legacy_sem = tmp_path / STORE_DIR / "semantic.json"
        legacy_sem.parent.mkdir(parents=True, exist_ok=True)
        legacy_sem.write_text(
            SemanticLayer(data_flow="legacy").model_dump_json(),
            encoding="utf-8",
        )

        # Trigger migration via load
        SemanticStore(tmp_path).load()

        assert legacy_sem.exists(), "legacy file should be retained"
        assert (tmp_path / STORE_DIR / PROJECT_FILE).exists()
