"""Unit tests for MCP tool implementations."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.mcp.tools import (
    _one_liner,
    _section_functions_graph,
    _section_hotspots,
    _section_map,
    _section_rules_list,
    _tool_before_create,
    _tool_orient,
    _tool_scope,
)
from winkers.resolver import CrossFileResolver

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# map section
# ---------------------------------------------------------------------------

def test_map_has_zones(graph, tmp_path):
    result = _section_map(graph, None, tmp_path)
    assert "zones" in result
    assert len(result["zones"]) >= 1
    assert all("name" in z for z in result["zones"])
    assert all("files" in z for z in result["zones"])
    assert all("functions" in z for z in result["zones"])


def test_map_has_hotspots(graph, tmp_path):
    result = _section_map(graph, None, tmp_path)
    assert "hotspots_top5" in result
    for h in result["hotspots_top5"]:
        assert "locked" in h
        assert "fn" in h
        assert "call_count" in h


def test_map_total_counts(graph, tmp_path):
    result = _section_map(graph, None, tmp_path)
    assert result["total_files"] == len(graph.files)
    assert result["total_functions"] == len(graph.functions)


def test_map_zone_filter(graph, tmp_path):
    result = _section_map(graph, "modules", tmp_path)
    assert "zones" in result
    assert all(z["name"] == "modules" for z in result["zones"])


# ---------------------------------------------------------------------------
# scope — function
# ---------------------------------------------------------------------------

def test_scope_function_locked(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    assert "function" in result
    assert result["function"]["locked"] is True
    assert len(result["callers"]) >= 2


def test_scope_function_callers_have_fields(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    for c in result["callers"]:
        assert "fn" in c
        assert "file" in c
        assert "line" in c
        assert "expression" in c
        assert "confidence" in c


def test_scope_function_callers_constraint(graph):
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    assert "callers_constraint" in result
    assert "safe_changes" in result["callers_constraint"]
    assert "breaking_changes" in result["callers_constraint"]
    assert "callers_expect" in result["callers_constraint"]


def test_scope_function_semantic_context(graph, tmp_path):
    """scope() includes semantic context when semantic.json exists."""
    from winkers.semantic import SemanticLayer, SemanticStore, ZoneIntent

    layer = SemanticLayer(
        data_flow="User -> API -> DB",
        zone_intents={"modules": ZoneIntent(why="business logic", wrong_approach="put SQL here")},
    )
    SemanticStore(tmp_path).save(layer)
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"}, root=tmp_path)
    assert "semantic" in result
    assert result["semantic"]["data_flow"] == "User -> API -> DB"
    assert result["semantic"]["zone_intent"]["why"] == "business logic"


def test_scope_function_no_semantic(graph, tmp_path):
    """scope() omits semantic key when no semantic.json."""
    result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"}, root=tmp_path)
    assert "semantic" not in result


def test_scope_function_free(graph):
    result = _tool_scope(graph, {"function": "modules/inventory.py::reserve_items"})
    assert result["function"]["locked"] is False
    assert len(result["callers"]) == 0


def test_scope_function_by_short_name(graph):
    result = _tool_scope(graph, {"function": "calculate_price"})
    assert "function" in result


def test_scope_function_not_found(graph):
    result = _tool_scope(graph, {"function": "nonexistent::fn"})
    assert "error" in result


# ---------------------------------------------------------------------------
# scope — file
# ---------------------------------------------------------------------------

def test_scope_file(graph):
    result = _tool_scope(graph, {"file": "modules/pricing.py"})
    assert "functions" in result
    assert len(result["functions"]) >= 3


def test_scope_file_fields(graph):
    result = _tool_scope(graph, {"file": "modules/pricing.py"})
    assert "language" in result
    assert "loc" in result
    for fn in result["functions"]:
        assert "locked" in fn
        assert "callers" in fn


def test_scope_file_coupling_fields(graph):
    """scope(file=) exposes sibling_imports / imported_by / migration_cost (0.8.1)."""
    result = _tool_scope(graph, {"file": "modules/pricing.py"})
    assert "sibling_imports" in result
    assert "imported_by" in result
    assert "migration_cost" in result
    # pricing.py is imported by api/prices.py and modules/inventory.py.
    assert "api/prices.py" in result["imported_by"]
    assert "modules/inventory.py" in result["imported_by"]
    assert result["migration_cost"] == len(result["imported_by"])
    # pricing.py itself imports nothing from its own zone.
    assert result["sibling_imports"] == 0


def test_scope_file_sibling_imports_nonzero(graph):
    """inventory.py imports pricing.py from the same zone → sibling_imports ≥ 1."""
    result = _tool_scope(graph, {"file": "modules/inventory.py"})
    assert result["sibling_imports"] >= 1


def test_scope_no_args(graph):
    result = _tool_scope(graph, {})
    assert "error" in result


# ---------------------------------------------------------------------------
# before_create — intent categorization (0.8.1)
# ---------------------------------------------------------------------------

def test_before_create_create_category(graph, tmp_path):
    """Creation keywords route to FTS5 fallback and return intent_type=create."""
    result = _tool_before_create(graph, {"intent": "add batch discount feature"}, tmp_path)
    assert result.get("intent_type") == "create"
    assert "existing" in result
    assert "matches" in result


def test_before_create_change_files_only(graph, tmp_path):
    """Structural intent with only file/zone targets → files block, no functions block."""
    result = _tool_before_create(
        graph, {"intent": "consolidate modules/ files into rules.py"}, tmp_path,
    )
    assert result.get("intent_type") == "change"
    assert result["resolved_targets"]["files"]
    assert result["resolved_targets"]["functions"] == []
    assert "files" in result
    assert "migration_cost" in result["files"]
    assert "safe_alternative" in result["files"]
    # No fn block expected when no fns are explicitly named (zone expansion of
    # locked fns produces a functions block too — both are valid). Allow either.
    if "functions" in result:
        assert "affected_fns" in result["functions"]


def test_before_create_change_function_only(graph, tmp_path):
    """In-place intent naming a function → functions block with caller expressions."""
    result = _tool_before_create(
        graph, {"intent": "rename calculate_price to compute_price"}, tmp_path,
    )
    assert result.get("intent_type") == "change"
    assert "modules/pricing.py::calculate_price" in result["resolved_targets"]["functions"]
    assert "functions" in result
    names = [fn["name"] for fn in result["functions"]["affected_fns"]]
    assert "calculate_price" in names
    target = next(
        fn for fn in result["functions"]["affected_fns"]
        if fn["name"] == "calculate_price"
    )
    assert target["locked"] is True
    assert target["callers_count"] >= 1
    assert all("expression" in c for c in target["callers"])


def test_before_create_change_mixed_targets(graph, tmp_path):
    """Intent naming both file and function → both blocks present."""
    result = _tool_before_create(
        graph, {"intent": "refactor calculate_price in pricing.py"}, tmp_path,
    )
    assert result.get("intent_type") == "change"
    assert result["resolved_targets"]["files"]
    assert result["resolved_targets"]["functions"]
    assert "files" in result
    assert "functions" in result


def test_before_create_change_surfaces_intent(graph, tmp_path):
    """affected_fns include fn.intent when LLM-generated description exists."""
    # Inject an intent on calculate_price for this test
    fn = graph.functions["modules/pricing.py::calculate_price"]
    saved = fn.intent
    fn.intent = "computes final price for an item with applicable discounts"
    try:
        result = _tool_before_create(
            graph, {"intent": "rename calculate_price to compute_price"}, tmp_path,
        )
        affected = result["functions"]["affected_fns"]
        target = next(e for e in affected if e["name"] == "calculate_price")
        assert target.get("intent") == fn.intent
    finally:
        fn.intent = saved


def test_scope_file_surfaces_intent(graph):
    """scope(file=) functions[] include intent when present."""
    fn = graph.functions["modules/pricing.py::calculate_price"]
    saved = fn.intent
    fn.intent = "computes final price for an item with applicable discounts"
    try:
        result = _tool_scope(graph, {"file": "modules/pricing.py"})
        target = next(f for f in result["functions"] if f["name"] == "calculate_price")
        assert target.get("intent") == fn.intent
    finally:
        fn.intent = saved


def test_scope_function_surfaces_intent(graph):
    """scope(function=) function entry includes intent when present."""
    fn = graph.functions["modules/pricing.py::calculate_price"]
    saved = fn.intent
    fn.intent = "computes final price for an item with applicable discounts"
    try:
        result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
        assert result["function"].get("intent") == fn.intent
    finally:
        fn.intent = saved


def test_before_create_unknown_category(graph, tmp_path):
    """When no keywords and no targets, returns orient-lite architectural context."""
    result = _tool_before_create(graph, {"intent": "clean up the code"}, tmp_path)
    assert result.get("intent_type") in ("unknown", "create")
    assert any(k in result for k in ("hotspots", "existing"))


# ---------------------------------------------------------------------------
# functions_graph section
# ---------------------------------------------------------------------------

def test_functions_graph_indexed(graph):
    result = _section_functions_graph(graph, None)
    assert "functions" in result
    assert "total" in result
    for key in result["functions"]:
        assert key.isdigit()


def test_functions_graph_callers_are_indices(graph):
    result = _section_functions_graph(graph, None)
    all_indices = set(result["functions"].keys())
    for entry in result["functions"].values():
        if "callers" in entry:
            for caller_idx in entry["callers"]:
                assert str(caller_idx) in all_indices


def test_functions_graph_zone_filter(graph):
    result = _section_functions_graph(graph, "modules")
    for entry in result["functions"].values():
        assert "modules" in entry["file"]


# ---------------------------------------------------------------------------
# hotspots section
# ---------------------------------------------------------------------------

def test_hotspots_default_threshold(graph):
    result = _section_hotspots(graph, 10)
    assert "hotspots" in result
    assert result["min_callers"] == 10
    assert isinstance(result["count"], int)


def test_hotspots_low_threshold(graph):
    result = _section_hotspots(graph, 1)
    assert result["count"] >= 1
    for h in result["hotspots"]:
        assert "function" in h
        assert "callers" in h
        assert len(h["callers"]) >= 1
        assert "expression" in h["callers"][0]


# ---------------------------------------------------------------------------
# rules_list section (0.8.1)
# ---------------------------------------------------------------------------

def _write_rules(tmp_path, rules):
    from winkers.conventions import RulesFile, RulesStore

    RulesStore(tmp_path).save(RulesFile(rules=rules))


def test_rules_list_includes_wrong_approach_snippet(tmp_path):
    from winkers.conventions import ConventionRule

    rule = ConventionRule(
        id=4,
        category="numeric",
        title="Decimal precision consistency",
        content="Use Decimal for money. Never cast to float mid-calculation.",
        wrong_approach=(
            "Converting Decimal to float in mid-pipeline: you lose precision, and "
            "downstream formatters silently round — totals drift by cents at scale."
        ),
        source="auto-detected",
        created="2026-04-15",
    )
    _write_rules(tmp_path, [rule])

    result = _section_rules_list(tmp_path)

    assert result["total"] == 1
    numeric = result["categories"]["numeric"]
    assert len(numeric) == 1
    entry = numeric[0]
    assert entry["id"] == 4
    assert entry["title"] == "Decimal precision consistency"
    assert "wrong_approach" in entry
    assert entry["wrong_approach"].startswith("Converting Decimal to float")
    # single-line
    assert "\n" not in entry["wrong_approach"]


def test_rules_list_omits_wrong_approach_when_empty(tmp_path):
    from winkers.conventions import ConventionRule

    rule = ConventionRule(
        id=7,
        category="api",
        title="No global state in handlers",
        content="Handlers must not mutate module-level state.",
        source="manual",
        created="2026-04-15",
    )
    _write_rules(tmp_path, [rule])

    entry = _section_rules_list(tmp_path)["categories"]["api"][0]
    assert "wrong_approach" not in entry


def test_one_liner_truncates_long_multiline():
    text = "First line is already long enough on its own.\n" + ("x" * 200)
    out = _one_liner(text, limit=80)
    assert "\n" not in out
    assert len(out) <= 80
    assert out.endswith("…")


# ---------------------------------------------------------------------------
# orient token budget
# ---------------------------------------------------------------------------

def test_orient_truncates_large_response(graph, tmp_path):
    """When max_tokens is tiny, orient truncates and adds a hint."""
    result = _tool_orient(
        graph,
        {"include": ["map", "functions_graph", "hotspots"], "max_tokens": 50},
        tmp_path,
    )
    # map should be included (highest priority, always fits first)
    assert "map" in result
    # At least one section should have been skipped
    assert result.get("_truncated") is True
    assert "_hint" in result


def test_orient_no_truncation_within_budget(graph, tmp_path):
    result = _tool_orient(
        graph,
        {"include": ["map"], "max_tokens": 50000},
        tmp_path,
    )
    assert "map" in result
    assert "_truncated" not in result


def test_orient_respects_priority_order(graph, tmp_path):
    """map has higher priority than functions_graph."""
    result = _tool_orient(
        graph,
        {"include": ["functions_graph", "map"], "max_tokens": 50},
        tmp_path,
    )
    # map should be present (higher priority), functions_graph skipped
    assert "map" in result


# ---------------------------------------------------------------------------
# orient.include coercion — workaround for clients that mis-serialise arrays
# ---------------------------------------------------------------------------

class TestOrientIncludeCoercion:
    def test_accepts_json_encoded_array_string(self, graph, tmp_path):
        """Sonnet sometimes sends include as '[\"map\",\"rules_list\"]'."""
        result = _tool_orient(
            graph, {"include": '["map", "hotspots"]'}, tmp_path,
        )
        assert "map" in result

    def test_accepts_single_string_as_one_element_array(self, graph, tmp_path):
        """Haiku sometimes sends a bare string when the spec wants an array."""
        result = _tool_orient(graph, {"include": "map"}, tmp_path)
        assert "map" in result

    def test_malformed_json_string_treated_as_section_name(self, graph, tmp_path):
        """'[map' isn't valid JSON — fall back to treating it as a name
        (which won't match any section, returning no data rather than crashing)."""
        result = _tool_orient(graph, {"include": "[map"}, tmp_path)
        # Doesn't match any real section; response is either empty-ish or has
        # just the meta fields (no map/hotspots/etc.).
        assert isinstance(result, dict)
        assert "map" not in result

    def test_empty_string_empty_list(self, graph, tmp_path):
        result = _tool_orient(graph, {"include": ""}, tmp_path)
        assert "map" not in result
        result2 = _tool_orient(graph, {"include": []}, tmp_path)
        assert "map" not in result2
