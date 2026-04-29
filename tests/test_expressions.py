"""Path 2 — AST expression-uses index tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from winkers.expressions import (
    KIND_CALL_ARG,
    KIND_COMPARISON,
    KIND_DICT_KEY,
    KIND_MATCH,
    KIND_SUBSCRIPT,
    ExpressionsIndex,
    ExpressionsStore,
    build_expressions_index,
)
from winkers.graph import GraphBuilder
from winkers.models import ValueLockedCollection
from winkers.resolver import CrossFileResolver
from winkers.value_locked import detect_value_locked, diff_collections


def _build_graph(root: Path):
    g = GraphBuilder().build(root)
    CrossFileResolver().resolve(g, str(root))
    detect_value_locked(g, root)
    return g


def _seed_status_repo(root: Path, body: str) -> None:
    """Convenience: every test repo has a `VALID_STATUSES = {...}` collection
    plus an arbitrary body that references the values."""
    (root / "status.py").write_text(
        'VALID_STATUSES = {"draft", "sent", "paid"}\n\n'
        "def is_valid(s): return s in VALID_STATUSES\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Visitor — syntactic classification
# ---------------------------------------------------------------------------


class TestSyntacticClassification:
    def test_classifies_comparison(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        # 4 occurrences so the threshold (≥3) is satisfied.
        body = (
            "def check_a(s): return s == 'sent'\n"
            "def check_b(s): return s == 'sent'\n"
            "def check_c(s): return s == 'sent'\n"
            "def check_d(s): return s == 'sent'\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        assert "sent" in idx.values
        kinds = {use.kind for use in idx.values["sent"]}
        assert KIND_COMPARISON in kinds

    def test_classifies_membership(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        body = (
            "def f1(s): return s in {'sent', 'paid', 'draft'}\n"
            "def f2(s): return s in {'sent', 'paid'}\n"
            "def f3(s): return s in ('sent', 'draft')\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        # `s in {"sent", ...}` — the literal is inside a Set inside a
        # Compare; classifier walks up one level.
        assert KIND_COMPARISON in {u.kind for u in idx.values["sent"]}

    def test_classifies_call_arg(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        body = (
            "def f1(): return is_valid('sent')\n"
            "def f2(): return is_valid('sent')\n"
            "def f3(): return is_valid('sent')\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        assert KIND_CALL_ARG in {u.kind for u in idx.values["sent"]}

    def test_classifies_dict_value_and_key(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        body = (
            "STATUS_LABEL = {'sent': 'Sent'}\n"
            "ANOTHER = {'sent': 'X'}\n"
            "ONE_MORE = {'sent': 'Y'}\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        kinds = {u.kind for u in idx.values["sent"]}
        # 'sent' as dict key → KIND_DICT_KEY
        assert KIND_DICT_KEY in kinds

    def test_classifies_subscript(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        body = (
            "def f1(d): return d['sent']\n"
            "def f2(d): return d['sent']\n"
            "def f3(d): return d['sent']\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        assert KIND_SUBSCRIPT in {u.kind for u in idx.values["sent"]}

    def test_classifies_match(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        body = (
            "def f1(s):\n"
            "    match s:\n"
            "        case 'sent': return 1\n"
            "        case 'draft': return 2\n"
            "def f2(s):\n"
            "    match s:\n"
            "        case 'sent': return 1\n"
            "def f3(s):\n"
            "    match s:\n"
            "        case 'sent': return 1\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        assert KIND_MATCH in {u.kind for u in idx.values["sent"]}


# ---------------------------------------------------------------------------
# Threshold + tracked-set scoping
# ---------------------------------------------------------------------------


class TestScopeAndThreshold:
    def test_only_tracked_values_indexed(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        # "frobnicate" isn't in any value_locked collection — must NOT
        # appear in the index even if it occurs N times.
        body = (
            "def f1(): return x == 'frobnicate'\n"
            "def f2(): return x == 'frobnicate'\n"
            "def f3(): return x == 'frobnicate'\n"
            "def g1(): return x == 'sent'\n"
            "def g2(): return x == 'sent'\n"
            "def g3(): return x == 'sent'\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        assert "frobnicate" not in idx.values
        assert "sent" in idx.values

    def test_below_threshold_dropped(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        # 'sent' only twice — below the ≥3 threshold.
        body = (
            "def f(): return s == 'sent'\n"
            "def g(): return s == 'sent'\n"
            "def h(): return s == 'paid'\n"
            "def i(): return s == 'paid'\n"
            "def j(): return s == 'paid'\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        assert "sent" not in idx.values
        assert "paid" in idx.values

    def test_extra_values_param(self, tmp_path: Path):
        """`extra_values` lets callers track non-value_locked literals."""
        proj = tmp_path / "proj"
        proj.mkdir()
        body = (
            "def f(): return x == 'custom'\n"
            "def g(): return x == 'custom'\n"
            "def h(): return x == 'custom'\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj, extra_values={"custom"})
        assert "custom" in idx.values

    def test_no_tracked_values_returns_empty(self, tmp_path: Path):
        """Empty graph → empty index, no AST walk performed."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "any.py").write_text(
            "def f(): return 'anything' == x\n", encoding="utf-8",
        )
        g = _build_graph(proj)
        # No value_locked collections → nothing to track.
        idx = build_expressions_index(g, proj)
        assert idx.values == {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        store = ExpressionsStore(tmp_path)
        idx = ExpressionsIndex(content_hash="abc")
        idx.values["sent"] = []
        store.save(idx)
        loaded = store.load()
        assert loaded is not None
        assert loaded.content_hash == "abc"
        assert "sent" in loaded.values

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert ExpressionsStore(tmp_path).load() is None

    def test_load_malformed_returns_none(self, tmp_path: Path):
        store = ExpressionsStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("not json", encoding="utf-8")
        assert store.load() is None


# ---------------------------------------------------------------------------
# diff_collections integration: index takes precedence over grep
# ---------------------------------------------------------------------------


class TestDiffCollectionsIntegration:
    @pytest.fixture
    def fixture_with_index(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        # Source where 'sent' appears in 4 distinct comparisons.
        body = (
            "def a(s): return s == 'sent'\n"
            "def b(s): return s == 'sent'\n"
            "def c(s): return s == 'sent'\n"
            "def d(s): return s == 'sent'\n"
        )
        _seed_status_repo(proj, body)
        g = _build_graph(proj)
        idx = build_expressions_index(g, proj)
        ExpressionsStore(proj).save(idx)
        return proj

    def test_diff_uses_ast_index_when_available(self, fixture_with_index):
        proj = fixture_with_index
        before = [
            ValueLockedCollection(
                name="VALID_STATUSES", file="status.py", line=1, kind="set",
                values=["draft", "sent", "paid"],
                literal_uses={}, files_with_uses=[],
            ),
        ]
        after = [
            ValueLockedCollection(
                name="VALID_STATUSES", file="status.py", line=1, kind="set",
                values=["draft", "paid"],
                literal_uses={}, files_with_uses=[],
            ),
        ]
        changes = diff_collections(before, after, root=proj)
        assert len(changes) == 1
        hits = changes[0].get("string_literal_hits") or {}
        assert hits.get("total", 0) >= 4
        # service.py is the file with the comparisons.
        files = hits.get("files") or []
        assert "service.py" in files
