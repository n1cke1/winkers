"""Tests for the impact-pass reuse decision in the units pipeline.

The unit-author pipeline reuses descriptions written earlier by the
impact pass (Wave 4d) to avoid a second `claude --print` call. The
reuse predicate must NOT fire when:
  - there's nothing to reuse (no unit, or unit without a description)
  - `--force-units` was passed (explicit re-author request)
  - the existing description is non-English (would silently break the
    EN-only embedding contract from Issue 2)

We test the predicate directly. Integration with the full pipeline
gets exercised by `winkers init --with-units` against a real fixture —
ast_hash plumbing and scanner mocks would only obscure the gate.
"""

from __future__ import annotations

from winkers.cli.init_pipeline.units import _impact_unit_reusable


class TestImpactUnitReusable:
    def test_none_existing_not_reusable(self):
        assert _impact_unit_reusable(None, force=False) is False

    def test_unit_without_description_not_reusable(self):
        assert _impact_unit_reusable(
            {"id": "x", "kind": "function_unit"}, force=False,
        ) is False

    def test_unit_with_empty_description_not_reusable(self):
        assert _impact_unit_reusable(
            {"id": "x", "description": ""}, force=False,
        ) is False

    def test_english_description_is_reusable(self):
        unit = {
            "id": "x",
            "description": (
                "Builds the X for Y. Called from Z when W happens. "
                "Edits to argument validation propagate to caller K."
            ),
        }
        assert _impact_unit_reusable(unit, force=False) is True

    def test_force_overrides_english_reuse(self):
        unit = {
            "id": "x",
            "description": "Builds the X for Y. Pure English description.",
        }
        assert _impact_unit_reusable(unit, force=True) is False

    def test_cyrillic_description_not_reusable(self):
        unit = {
            "id": "x",
            "description": (
                "Строит X для Y. Вызывается из Z при наступлении W."
            ),
        }
        assert _impact_unit_reusable(unit, force=False) is False

    def test_mixed_above_threshold_not_reusable(self):
        unit = {
            "id": "x",
            "description": "Builds X for Y используя коллекторы пара config.",
        }
        assert _impact_unit_reusable(unit, force=False) is False

    def test_english_with_single_domain_term_below_threshold_reusable(self):
        # has_cyrillic uses a 5% ratio — one short Cyrillic
        # identifier in long English prose stays below threshold.
        unit = {
            "id": "x",
            "description": (
                "Builds the steam-collector layout for the topology graph. "
                "Called by compute_scenario_derived during T-TOPO-3c "
                "construction; consumes the upstream pressure setpoint and "
                "writes a Sink+Source pair per active boiler. Edits to "
                "the source-mapping constants in `boiler_layout.py` "
                "propagate here through the connection labels (1 verbatim "
                "domain term in otherwise English prose: котёл)."
            ),
        }
        assert _impact_unit_reusable(unit, force=False) is True
