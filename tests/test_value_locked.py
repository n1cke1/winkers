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
# value_unit promotion (Wave 4b)
# ---------------------------------------------------------------------------

class TestValueUnitPromotion:
    """`build_value_units(graph, root)` converts the detector's
    `value_locked_collections` into `units.json` records."""

    def _build(self, root: Path):
        g = GraphBuilder().build(root)
        CrossFileResolver().resolve(g, str(root))
        from winkers.value_locked import detect_value_locked
        detect_value_locked(g, root)
        return g

    def test_id_format(self):
        from winkers.value_locked import value_unit_id
        assert value_unit_id("status.py", "VALID_STATUSES") == (
            "value:status.py::VALID_STATUSES"
        )

    def test_emits_one_unit_per_collection(self, tmp_path: Path):
        from winkers.value_locked import build_value_units
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "status.py").write_text(
            'VALID_STATUSES = {"draft", "sent", "paid"}\n'
            'PRIORITIES = {1, 2, 3}\n\n'
            'def is_valid(s): return s in VALID_STATUSES\n',
            encoding="utf-8",
        )
        g = self._build(proj)
        units = build_value_units(g, proj)
        # Two collections → two units
        ids = sorted(u["id"] for u in units)
        assert ids == [
            "value:status.py::PRIORITIES",
            "value:status.py::VALID_STATUSES",
        ]

    def test_unit_shape_basics(self, tmp_path: Path):
        from winkers.value_locked import build_value_units
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "status.py").write_text(
            'VALID_STATUSES = {"draft", "sent", "paid"}\n\n'
            'def is_valid(s): return s in VALID_STATUSES\n',
            encoding="utf-8",
        )
        g = self._build(proj)
        units = build_value_units(g, proj)
        u = next(
            x for x in units if x["id"] == "value:status.py::VALID_STATUSES"
        )
        assert u["kind"] == "value_unit"
        assert u["name"] == "VALID_STATUSES"
        assert u["anchor"]["file"] == "status.py"
        assert u["anchor"]["line"] >= 1
        assert set(u["values"]) == {"draft", "sent", "paid"}
        assert u["consumer_count"] >= 1
        assert "status.py" in u["consumer_files"]
        assert len(u["source_hash"]) == 64  # SHA-256 hex
        assert u["description"] == ""  # filled later by Wave 4c

    def test_summary_includes_value_names(self, tmp_path: Path):
        """Summary surfaces value names so embeddings can match queries
        like 'status enum' even before LLM description lands."""
        from winkers.value_locked import build_value_units
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "status.py").write_text(
            'VALID_STATUSES = {"draft", "sent", "paid"}\n\n'
            'def is_valid(s): return s in VALID_STATUSES\n',
            encoding="utf-8",
        )
        g = self._build(proj)
        units = build_value_units(g, proj)
        u = units[0]
        assert "VALID_STATUSES" in u["summary"]
        # All three values appear as repr'd strings in the summary
        for v in ("'draft'", "'sent'", "'paid'"):
            assert v in u["summary"]

    def test_summary_truncates_at_six_values(self, tmp_path: Path):
        from winkers.value_locked import build_value_units
        proj = tmp_path / "proj"
        proj.mkdir()
        big = ", ".join(f'"v{i}"' for i in range(10))
        (proj / "status.py").write_text(
            f"BIG = {{{big}}}\n\n"
            "def is_valid(s): return s in BIG\n",
            encoding="utf-8",
        )
        g = self._build(proj)
        units = build_value_units(g, proj)
        u = units[0]
        assert "(+4)" in u["summary"]  # 10 - 6 = 4 hidden

    def test_cross_file_consumer_files_listed(self, tmp_path: Path):
        from winkers.value_locked import build_value_units
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "status.py").write_text(
            'VALID_STATUSES = {"draft", "sent"}\n\n'
            "def is_valid(s): return s in VALID_STATUSES\n",
            encoding="utf-8",
        )
        (proj / "service.py").write_text(
            "from status import VALID_STATUSES\n\n"
            "def normalize(x):\n"
            "    if x not in VALID_STATUSES:\n"
            "        return 'draft'\n"
            "    return x\n",
            encoding="utf-8",
        )
        g = self._build(proj)
        units = build_value_units(g, proj)
        u = next(x for x in units if x["name"] == "VALID_STATUSES")
        assert "status.py" in u["consumer_files"]
        assert "service.py" in u["consumer_files"]
        assert u["consumer_count"] >= 2


# ---------------------------------------------------------------------------
# Cross-file consumer detection (Wave 3.5 — Gap 2 fix)
# ---------------------------------------------------------------------------

class TestCrossFileConsumers:
    """`_find_referencing_fns` now walks `graph.import_edges` so consumer
    functions in modules OTHER than the collection's defining file are
    captured in `referenced_by_fns`."""

    def _build(self, root: Path):
        g = GraphBuilder().build(root)
        CrossFileResolver().resolve(g, str(root))
        detect_value_locked(g, root)
        return g

    def _seed_status_module(self, proj: Path) -> None:
        """Status module with a function so GraphBuilder includes the file
        (collection-only files are otherwise pruned)."""
        (proj / "status.py").write_text(
            'VALID_STATUSES = {"draft", "sent", "paid"}\n\n'
            "def is_valid(s: str) -> bool:\n"
            "    return s in VALID_STATUSES\n",
            encoding="utf-8",
        )

    def test_cross_module_consumer_in_referenced_by(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        self._seed_status_module(proj)
        (proj / "service.py").write_text(
            "from status import VALID_STATUSES\n\n"
            "def normalize(status: str) -> str:\n"
            '    if status not in VALID_STATUSES:\n'
            '        return "draft"\n'
            "    return status\n",
            encoding="utf-8",
        )
        g = self._build(proj)
        col = next(
            c for c in g.value_locked_collections if c.name == "VALID_STATUSES"
        )
        # `service::normalize` is in another file but imports + uses
        # VALID_STATUSES — must show up in referenced_by_fns now.
        assert any("service.py::normalize" in fid for fid in col.referenced_by_fns)

    def test_cross_module_caller_literal_uses_counted(self, tmp_path: Path):
        """End-to-end: when a cross-module consumer exists, callers passing
        literals from the collection also get counted via the existing
        pass-2 walk over `call_edges`."""
        proj = tmp_path / "proj"
        proj.mkdir()
        self._seed_status_module(proj)
        (proj / "service.py").write_text(
            "from status import VALID_STATUSES\n\n"
            "def normalize(status: str) -> str:\n"
            '    if status not in VALID_STATUSES:\n'
            '        return "draft"\n'
            "    return status\n",
            encoding="utf-8",
        )
        (proj / "caller.py").write_text(
            "from service import normalize\n\n"
            "def main():\n"
            '    return normalize("sent")\n',
            encoding="utf-8",
        )
        g = self._build(proj)
        col = next(
            c for c in g.value_locked_collections if c.name == "VALID_STATUSES"
        )
        # Caller passes "sent" to normalize() which is now a registered
        # consumer of VALID_STATUSES → "sent" must be in literal_uses.
        assert col.literal_uses.get("sent", 0) >= 1

    def test_unrelated_import_no_body_match(self, tmp_path: Path):
        """Files that import from the module but never mention the
        collection name in their bodies are skipped by the body-match
        pre-filter, so unrelated functions don't pollute
        referenced_by_fns."""
        proj = tmp_path / "proj"
        proj.mkdir()
        self._seed_status_module(proj)
        (proj / "other.py").write_text(
            "from status import is_valid\n\n"
            "def use_other():\n"
            "    return is_valid('draft')\n",
            encoding="utf-8",
        )
        g = self._build(proj)
        col = next(
            c for c in g.value_locked_collections if c.name == "VALID_STATUSES"
        )
        # `other.py::use_other` doesn't textually reference VALID_STATUSES
        # — must not be added to referenced_by_fns.
        assert not any(
            "other.py::use_other" in fid for fid in col.referenced_by_fns
        )


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
# Path 1 literal-blind: count_string_literal_occurrences + string_literal_hits
# ---------------------------------------------------------------------------

class TestStringLiteralScan:
    def test_no_values_returns_empty(self, tmp_path):
        from winkers.value_locked import count_string_literal_occurrences
        assert count_string_literal_occurrences([], tmp_path) == {}

    def test_finds_quoted_string_in_python(self, tmp_path):
        from winkers.value_locked import count_string_literal_occurrences
        (tmp_path / "repo.py").write_text(
            'def is_active(s):\n    return s == "sent"\n',
            encoding="utf-8",
        )
        hits = count_string_literal_occurrences(["sent"], tmp_path)
        assert "sent" in hits
        assert len(hits["sent"]) == 1
        rel, line, snippet = hits["sent"][0]
        assert rel == "repo.py"
        assert line == 2
        assert "sent" in snippet

    def test_finds_quoted_string_in_sql(self, tmp_path):
        from winkers.value_locked import count_string_literal_occurrences
        (tmp_path / "fixtures.sql").write_text(
            "INSERT INTO invoices (status) VALUES ('paid'), ('void');\n",
            encoding="utf-8",
        )
        hits = count_string_literal_occurrences(["paid", "void"], tmp_path)
        assert len(hits["paid"]) == 1
        assert len(hits["void"]) == 1

    def test_skips_unquoted_substring(self, tmp_path):
        """Bare identifier match is NOT a literal hit."""
        from winkers.value_locked import count_string_literal_occurrences
        (tmp_path / "repo.py").write_text(
            "is_sent_today = True\nstatus_sent_count = 5\n",
            encoding="utf-8",
        )
        hits = count_string_literal_occurrences(["sent"], tmp_path)
        assert hits["sent"] == []

    def test_excludes_vendored_dirs(self, tmp_path):
        from winkers.value_locked import count_string_literal_occurrences
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "junk.js").write_text(
            'const x = "sent"\n',
            encoding="utf-8",
        )
        # Should be skipped — 0 hits.
        hits = count_string_literal_occurrences(["sent"], tmp_path)
        assert hits["sent"] == []

    def test_diff_collections_with_root_attaches_string_hits(self, tmp_path):
        from winkers.value_locked import diff_collections
        # Set up a tiny repo that references "sent" in a bare comparison.
        (tmp_path / "service.py").write_text(
            'def is_sent(invoice):\n    return invoice.status == "sent"\n',
            encoding="utf-8",
        )
        before = [
            ValueLockedCollection(
                name="VALID_STATUSES", file="status.py", line=1, kind="set",
                values=["draft", "sent", "paid"],
                literal_uses={},
                files_with_uses=[],
            )
        ]
        after = [
            ValueLockedCollection(
                name="VALID_STATUSES", file="status.py", line=1, kind="set",
                values=["draft", "paid"],
                literal_uses={},
                files_with_uses=[],
            )
        ]
        changes = diff_collections(before, after, root=tmp_path)
        assert len(changes) == 1
        assert changes[0]["removed"] == ["sent"]
        # The repo-wide scan should pick up the bare comparison.
        hits = changes[0].get("string_literal_hits")
        assert hits is not None
        assert hits["total"] == 1
        assert "service.py" in hits["files"]
        assert "sent" in hits["by_value"]

    def test_diff_collections_without_root_no_string_hits(self):
        from winkers.value_locked import diff_collections
        before = [
            ValueLockedCollection(
                name="S", file="x.py", line=1, kind="set",
                values=["a", "b"], literal_uses={"a": 2},
                files_with_uses=["y.py"],
            )
        ]
        after = [
            ValueLockedCollection(
                name="S", file="x.py", line=1, kind="set",
                values=["a"], literal_uses={"a": 2},
                files_with_uses=["y.py"],
            )
        ]
        changes = diff_collections(before, after)  # no root → no scan
        assert "string_literal_hits" not in changes[0]


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
        assert "total_literal_uses" in warning

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
