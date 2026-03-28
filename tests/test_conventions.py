"""Tests for conventions data model, RulesStore, compile_overview, TraceLogger."""

from __future__ import annotations

import json

from winkers.conventions import (
    ConventionRule,
    DismissedStore,
    RuleAdd,
    RulesAudit,
    RulesConfig,
    RulesFile,
    RulesStore,
    RuleStats,
    TraceLogger,
    compile_overview,
)


def _rule(id: int, category: str, source: str = "manual") -> ConventionRule:
    return ConventionRule(
        id=id,
        category=category,
        title=f"{category} rule",
        content=f"Always do {category} correctly. Second sentence.",
        source=source,  # type: ignore[arg-type]
        created="2026-03-28",
    )


# ---------------------------------------------------------------------------
# RulesStore
# ---------------------------------------------------------------------------

def test_load_empty(tmp_path):
    store = RulesStore(tmp_path)
    rf = store.load()
    assert rf.rules == []
    assert rf.version == 1


def test_save_and_load(tmp_path):
    store = RulesStore(tmp_path)
    rf = RulesFile(project="myapp", rules=[_rule(1, "data")])
    store.save(rf)
    loaded = store.load()
    assert loaded.project == "myapp"
    assert len(loaded.rules) == 1
    assert loaded.rules[0].category == "data"


def test_next_id_empty(tmp_path):
    store = RulesStore(tmp_path)
    assert store.next_id(RulesFile()) == 1


def test_next_id_with_rules(tmp_path):
    store = RulesStore(tmp_path)
    rf = RulesFile(rules=[_rule(1, "data"), _rule(5, "errors")])
    assert store.next_id(rf) == 6


def test_add_rule(tmp_path):
    store = RulesStore(tmp_path)
    store.add_rule(_rule(1, "data"))
    store.add_rule(_rule(2, "errors"))
    loaded = store.load()
    assert len(loaded.rules) == 2


def test_delete_rule(tmp_path):
    store = RulesStore(tmp_path)
    rf = RulesFile(rules=[_rule(1, "data"), _rule(2, "errors")])
    store.save(rf)
    deleted = store.delete_rule(1)
    assert deleted is True
    loaded = store.load()
    assert len(loaded.rules) == 1
    assert loaded.rules[0].id == 2


def test_delete_rule_not_found(tmp_path):
    store = RulesStore(tmp_path)
    store.save(RulesFile(rules=[_rule(1, "models")]))
    assert store.delete_rule(99) is False


def test_next_id_never_reuses(tmp_path):
    """After deleting rule #3, next_id should still be > 3."""
    store = RulesStore(tmp_path)
    rf = RulesFile(rules=[_rule(1, "data"), _rule(2, "errors"), _rule(3, "numeric")])
    store.save(rf)
    store.delete_rule(3)
    loaded = store.load()
    assert store.next_id(loaded) == 3  # max is now 2, so next is 3 — still > deleted


def test_exists(tmp_path):
    store = RulesStore(tmp_path)
    assert not store.exists()
    store.save(RulesFile())
    assert store.exists()


# ---------------------------------------------------------------------------
# compile_overview
# ---------------------------------------------------------------------------

def test_compile_overview_basic(tmp_path):
    rf = RulesFile(rules=[
        _rule(1, "data"),
        _rule(2, "errors"),
    ])
    out = tmp_path / "overview.md"
    compile_overview(rf, out)
    text = out.read_text(encoding="utf-8")
    assert "data" in text
    assert "errors" in text
    assert text.startswith("# Project conventions")


def test_compile_overview_one_per_category(tmp_path):
    """Two rules for same category — only one line in overview."""
    rf = RulesFile(rules=[
        _rule(1, "data", source="semantic-agent"),
        ConventionRule(
            id=2, category="data", title="data manual",
            content="Manual override.", source="manual", created="2026-03-28",
        ),
    ])
    out = tmp_path / "overview.md"
    compile_overview(rf, out)
    lines = [ln for ln in out.read_text().splitlines() if ln.startswith("- data")]
    assert len(lines) == 1


def test_compile_overview_token_budget(tmp_path):
    """With tiny budget, overview is trimmed."""
    rules = [_rule(i, f"category{i:03d}") for i in range(50)]
    rf = RulesFile(config=RulesConfig(overview_max_tokens=30), rules=rules)
    out = tmp_path / "overview.md"
    compile_overview(rf, out)
    text = out.read_text(encoding="utf-8")
    # Should not contain all 50 categories
    assert text.count("- category") < 50


def test_compile_overview_sorted(tmp_path):
    rf = RulesFile(rules=[_rule(1, "validation"), _rule(2, "architecture")])
    out = tmp_path / "overview.md"
    compile_overview(rf, out)
    lines = [ln for ln in out.read_text().splitlines() if ln.startswith("- ")]
    assert lines[0].startswith("- architecture")
    assert lines[1].startswith("- validation")


# ---------------------------------------------------------------------------
# TraceLogger
# ---------------------------------------------------------------------------

def test_trace_logger_writes_jsonl(tmp_path):
    logger = TraceLogger(tmp_path, "session-abc123")
    logger.log({"event": "orient", "topics_returned": ["models", "errors"]})
    logger.log({"event": "rule_read", "topic": "models", "rule_id": 1})

    traces_dir = tmp_path / ".winkers" / "rules" / "traces"
    files = list(traces_dir.glob("*.jsonl"))
    assert len(files) == 1

    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["event"] == "orient"
    assert "timestamp" in first
    assert first["topics_returned"] == ["models", "errors"]


def test_trace_logger_appends(tmp_path):
    logger = TraceLogger(tmp_path, "session-xyz")
    logger.log({"event": "orient"})
    logger.log({"event": "rule_read", "topic": "errors", "rule_id": 2})

    traces_dir = tmp_path / ".winkers" / "rules" / "traces"
    files = list(traces_dir.glob("*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_trace_logger_session_in_filename(tmp_path):
    logger = TraceLogger(tmp_path, "my-session-id")
    logger.log({"event": "orient"})
    traces_dir = tmp_path / ".winkers" / "rules" / "traces"
    files = list(traces_dir.glob("*.jsonl"))
    assert "my-session-id" in files[0].name


# ---------------------------------------------------------------------------
# ConventionRule model
# ---------------------------------------------------------------------------

def test_rule_stats_defaults():
    stats = RuleStats()
    assert stats.times_requested == 0
    assert stats.times_applied == 0
    assert stats.times_confident_wrong == 0


def test_rule_roundtrip():
    rule = ConventionRule(
        id=42,
        category="numeric",
        title="Monetary values",
        content="Store money as integer cents.",
        wrong_approach="Using float for money causes rounding errors.",
        related=["validation"],
        affects=["app/models/invoice.py", "billing"],
        source="manual",
        created="2026-03-28",
    )
    dumped = rule.model_dump_json()
    loaded = ConventionRule.model_validate_json(dumped)
    assert loaded.id == 42
    assert loaded.category == "numeric"
    assert loaded.related == ["validation"]
    assert loaded.affects == ["app/models/invoice.py", "billing"]
    assert loaded.wrong_approach == "Using float for money causes rounding errors."


def test_affects_defaults_empty():
    rule = _rule(1, "data")
    assert rule.affects == []


# ---------------------------------------------------------------------------
# DismissedStore
# ---------------------------------------------------------------------------

def test_dismissed_load_empty(tmp_path):
    store = DismissedStore(tmp_path)
    d = store.load()
    assert d.dismissed_adds == []
    assert d.dismissed_removes == []
    assert d.dismissed_updates == []


def test_dismissed_merge_adds(tmp_path):
    store = DismissedStore(tmp_path)
    adds = [RuleAdd(category="validation", title="@login_required", content="use it")]
    store.merge(adds, [], [])
    d = store.load()
    assert len(d.dismissed_adds) == 1
    assert d.dismissed_adds[0].category == "validation"
    assert d.dismissed_adds[0].title == "@login_required"


def test_dismissed_merge_no_duplicates(tmp_path):
    store = DismissedStore(tmp_path)
    add = RuleAdd(category="validation", title="@login_required", content="use it")
    store.merge([add], [], [])
    store.merge([add], [], [])  # second merge should not duplicate
    d = store.load()
    assert len(d.dismissed_adds) == 1


def test_dismissed_merge_removes_and_updates(tmp_path):
    store = DismissedStore(tmp_path)
    store.merge([], [3, 5], [2])
    d = store.load()
    assert set(d.dismissed_removes) == {3, 5}
    assert set(d.dismissed_updates) == {2}


def test_dismissed_merge_accumulates(tmp_path):
    store = DismissedStore(tmp_path)
    store.merge([], [1], [])
    store.merge([], [2], [3])
    d = store.load()
    assert set(d.dismissed_removes) == {1, 2}
    assert set(d.dismissed_updates) == {3}


# ---------------------------------------------------------------------------
# RulesAudit
# ---------------------------------------------------------------------------

def test_rules_audit_is_empty():
    assert RulesAudit().is_empty()
    assert not RulesAudit(add=[RuleAdd(category="data", title="t", content="c")]).is_empty()
