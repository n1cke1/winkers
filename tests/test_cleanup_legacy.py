"""Tests for `winkers cleanup-legacy` — Wave 4 follow-up."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from winkers.cli.main import cli
from winkers.conventions import ConventionRule, RulesFile, RulesStore
from winkers.descriptions.store import UnitsStore
from winkers.impact.models import ImpactFile, ImpactReport
from winkers.impact.store import ImpactStore
from winkers.project import PROJECT_FILE
from winkers.semantic import SemanticLayer, SemanticStore
from winkers.store import STORE_DIR


def _seed_legacy_semantic(root: Path) -> Path:
    """Pre-Wave-4a state: ONLY semantic.json on disk."""
    p = root / STORE_DIR / "semantic.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        SemanticLayer(data_flow="legacy").model_dump_json(),
        encoding="utf-8",
    )
    return p


def _seed_legacy_rules(root: Path) -> Path:
    p = root / STORE_DIR / "rules" / "rules.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        RulesFile(rules=[ConventionRule(
            id=1, category="style", title="t", content="c",
            source="manual", created="2026-01-01",
        )]).model_dump_json(),
        encoding="utf-8",
    )
    return p


def _seed_legacy_impact(root: Path) -> Path:
    """Pre-Wave-4d state: only impact.json, no units.json."""
    p = root / STORE_DIR / "impact.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    impact = ImpactFile(functions={
        "x::f": ImpactReport(
            content_hash="abc",
            risk_level="high", risk_score=0.9, summary="x",
        ),
    })
    p.write_text(
        impact.model_dump_json(indent=2, exclude_defaults=False),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------


class TestCleanupLegacy:
    def test_does_nothing_when_no_legacy(self, tmp_path: Path):
        result = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert result.exit_code == 0
        assert "No legacy artifacts" in result.output

    def test_does_nothing_when_only_legacy_no_migration(self, tmp_path: Path):
        """If project.json is missing, semantic.json is the source of truth.
        Cleanup must NOT touch it — that would lose state."""
        sem = _seed_legacy_semantic(tmp_path)
        result = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert result.exit_code == 0
        assert "No legacy artifacts" in result.output
        assert sem.exists()  # untouched

    def test_removes_semantic_after_migration(self, tmp_path: Path):
        # Step 1: legacy on disk
        sem = _seed_legacy_semantic(tmp_path)
        # Step 2: migration via SemanticStore.load() builds project.json
        loaded = SemanticStore(tmp_path).load()
        assert loaded is not None
        assert (tmp_path / STORE_DIR / PROJECT_FILE).exists()
        # Step 3: cleanup removes the legacy file
        result = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not sem.exists()
        assert (tmp_path / STORE_DIR / PROJECT_FILE).exists()  # migrated copy stays

    def test_removes_rules_after_migration(self, tmp_path: Path):
        rules_legacy = _seed_legacy_rules(tmp_path)
        # Migration via RulesStore.load()
        RulesStore(tmp_path).load()
        result = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert result.exit_code == 0
        assert not rules_legacy.exists()

    def test_removes_impact_after_migration(self, tmp_path: Path):
        # Seed impact.json AND a units.json that carries the impact fields.
        # ImpactStore.save() on units.json is the migration handle.
        _seed_legacy_impact(tmp_path)
        # Trigger migration via ImpactStore.load() → folds into units.json
        loaded = ImpactStore(tmp_path).load()
        assert "x::f" in loaded.functions
        # units.json must now carry risk_level on the function_unit entry
        units = UnitsStore(tmp_path).load()
        assert any(u.get("risk_level") == "high" for u in units)
        # Cleanup removes legacy impact.json
        result = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert result.exit_code == 0
        assert not (tmp_path / STORE_DIR / "impact.json").exists()

    def test_dry_run_does_not_delete(self, tmp_path: Path):
        sem = _seed_legacy_semantic(tmp_path)
        SemanticStore(tmp_path).load()  # migrate
        assert sem.exists()
        result = CliRunner().invoke(
            cli, ["cleanup-legacy", str(tmp_path), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert sem.exists()  # still there

    def test_idempotent(self, tmp_path: Path):
        _seed_legacy_semantic(tmp_path)
        SemanticStore(tmp_path).load()
        # First run removes
        r1 = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert r1.exit_code == 0
        assert "Removed 1" in r1.output
        # Second run is a no-op
        r2 = CliRunner().invoke(cli, ["cleanup-legacy", str(tmp_path)])
        assert r2.exit_code == 0
        assert "No legacy artifacts" in r2.output
