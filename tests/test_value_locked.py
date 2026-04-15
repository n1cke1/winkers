"""Unit + integration tests for the value_locked detector and tool surfaces."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.mcp.tools import _tool_before_create, _tool_impact_check, _tool_scope
from winkers.resolver import CrossFileResolver
from winkers.value_locked import (
    ValueLockedCollection,
    detect_value_locked,
    diff_collections,
)

FIXTURE = Path(__file__).parent / "fixtures" / "value_locked_project"


@pytest.fixture
def graph(tmp_path):
    """Per-test graph from the value_locked fixture, copied into tmp_path so
    impact_check can mutate files without touching the real fixture."""
    import shutil

    project = tmp_path / "proj"
    shutil.copytree(FIXTURE, project)
    g = GraphBuilder().build(project)
    CrossFileResolver().resolve(g, str(project))
    detect_value_locked(g, project)
    return g, project


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class TestDetector:
    def test_detects_set_collection(self, graph):
        g, _ = graph
        names = [c.name for c in g.value_locked_collections]
        assert "VALID_STATUSES" in names

    def test_extracts_string_values(self, graph):
        g, _ = graph
        col = next(c for c in g.value_locked_collections if c.name == "VALID_STATUSES")
        assert set(col.values) == {"draft", "sent", "viewed", "paid", "void"}
        assert col.kind == "set"

    def test_finds_referencing_function(self, graph):
        g, _ = graph
        col = next(c for c in g.value_locked_collections if c.name == "VALID_STATUSES")
        # is_valid_status uses VALID_STATUSES in its body
        assert any("is_valid_status" in fid for fid in col.referenced_by_fns)

    def test_counts_caller_literal_uses(self, graph):
        g, _ = graph
        col = next(c for c in g.value_locked_collections if c.name == "VALID_STATUSES")
        # caller.is_known_paid → is_valid_status("paid"), so "paid" should
        # be counted at least once.
        assert col.literal_uses.get("paid", 0) >= 1
        assert any("caller.py" in f for f in col.files_with_uses)

    def test_does_not_detect_dict_collections(self, graph):
        """TRANSITIONS in the fixture is a dict, not a set — outside MVP scope."""
        g, _ = graph
        names = [c.name for c in g.value_locked_collections]
        assert "TRANSITIONS" not in names

    def test_size_cap(self, tmp_path):
        """Collections > 64 values are skipped (bounded cost)."""
        proj = tmp_path / "p"
        proj.mkdir()
        big = ", ".join(f'"v{i}"' for i in range(65))
        (proj / "big.py").write_text(f"BIG = {{{big}}}\n", encoding="utf-8")
        g = GraphBuilder().build(proj)
        CrossFileResolver().resolve(g, str(proj))
        detect_value_locked(g, proj)
        assert all(c.name != "BIG" for c in g.value_locked_collections)


# ---------------------------------------------------------------------------
# diff_collections
# ---------------------------------------------------------------------------

class TestDiff:
    def _col(self, name, values, uses=None):
        return ValueLockedCollection(
            name=name, file="x.py", line=1, kind="set",
            values=values,
            literal_uses=uses or {},
            files_with_uses=["caller.py"] if uses else [],
        )

    def test_no_change_no_record(self):
        before = [self._col("S", ["a", "b"], {"a": 3})]
        after = [self._col("S", ["a", "b"], {"a": 3})]
        assert diff_collections(before, after) == []

    def test_value_removed_records_change(self):
        before = [self._col("S", ["a", "b", "c"], {"a": 3, "b": 1})]
        after = [self._col("S", ["a"], {"a": 3})]
        changes = diff_collections(before, after)
        assert len(changes) == 1
        assert changes[0]["removed"] == ["b", "c"]
        assert changes[0]["affected_literal_uses"] == 1  # only "b" had uses

    def test_value_added_only_no_risk(self):
        before = [self._col("S", ["a"], {"a": 2})]
        after = [self._col("S", ["a", "b"], {"a": 2})]
        changes = diff_collections(before, after)
        assert changes[0]["added"] == ["b"]
        assert changes[0]["removed"] == []
        assert changes[0]["affected_literal_uses"] == 0


# ---------------------------------------------------------------------------
# scope(file=) integration
# ---------------------------------------------------------------------------

class TestScopeFileIntegration:
    def test_scope_file_includes_value_locked_section(self, graph):
        g, _ = graph
        result = _tool_scope(g, {"file": "status.py"})
        assert "value_locked_collections" in result
        names = [c["name"] for c in result["value_locked_collections"]]
        assert "VALID_STATUSES" in names

    def test_scope_file_skipped_when_no_collections(self, graph):
        g, _ = graph
        result = _tool_scope(g, {"file": "caller.py"})
        # caller.py has none of its own collections → field omitted
        assert "value_locked_collections" not in result


# ---------------------------------------------------------------------------
# before_create change → value_changes block
# ---------------------------------------------------------------------------

class TestBeforeCreateValueChanges:
    def test_simplify_intent_warns(self, graph):
        g, project = graph
        result = _tool_before_create(
            g, {"intent": "simplify status.py"}, project,
        )
        assert result.get("intent_type") == "change"
        assert "value_changes" in result
        assert "VALID_STATUSES" in result["value_changes"]
        warning = result["value_changes"]["VALID_STATUSES"]
        assert warning["value_locked"] is True
        assert "safe_alternative" in warning

    def test_additive_intent_does_not_warn(self, graph):
        g, project = graph
        # "extend" is a change keyword but not in _VALUE_REMOVAL_KEYWORDS
        result = _tool_before_create(
            g, {"intent": "extend status.py with new types"}, project,
        )
        assert result.get("intent_type") == "change"
        assert "value_changes" not in result

    def test_create_intent_does_not_warn(self, graph):
        g, project = graph
        result = _tool_before_create(
            g, {"intent": "add a new status validator"}, project,
        )
        assert "value_changes" not in result


# ---------------------------------------------------------------------------
# impact_check value-domain warning
# ---------------------------------------------------------------------------

class TestImpactCheckValueChange:
    def test_removed_value_surfaces_warning(self, graph):
        g, project = graph

        # Mutate status.py to remove "sent" and "viewed"
        status_path = project / "status.py"
        text = status_path.read_text(encoding="utf-8")
        text = text.replace(
            'VALID_STATUSES = {"draft", "sent", "viewed", "paid", "void"}',
            'VALID_STATUSES = {"draft", "paid", "void"}',
        )
        status_path.write_text(text, encoding="utf-8")

        result = _tool_impact_check(
            g, {"file_path": "status.py"}, project, lambda: g,
        )
        assert "value_changes" in result
        vc = result["value_changes"][0]
        assert vc["name"] == "VALID_STATUSES"
        assert set(vc["removed"]) == {"sent", "viewed"}
