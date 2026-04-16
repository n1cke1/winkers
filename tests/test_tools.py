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
    _tool_browse,
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


def test_before_create_function_only_derives_files_block(graph, tmp_path):
    """Function-only intent still yields file-level metrics via derived paths.

    The user didn't name a file, so resolved_targets.files stays empty — but
    the `files` block (migration_cost / imported_by / cross_imports) must
    still appear, derived from the resolved function's file.
    """
    result = _tool_before_create(
        graph, {"intent": "rename calculate_price to compute_price"}, tmp_path,
    )
    assert result["resolved_targets"]["files"] == []
    assert "files" in result
    assert "migration_cost" in result["files"]
    assert "cross_imports" in result["files"]
    # pricing.py is imported by api/prices.py and modules/inventory.py
    assert "api/prices.py" in result["files"]["imported_by"]


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
    """Unresolvable intent returns a compact actionable hint, not a hotspots dump."""
    result = _tool_before_create(graph, {"intent": "clean up the code"}, tmp_path)
    assert result.get("intent_type") in ("unknown", "create")
    if result.get("intent_type") == "unknown":
        assert "hint" in result
        assert "examples" in result
        # No architectural dump — those were noise for agents.
        assert "hotspots" not in result
        assert "zone_intents" not in result
    else:
        # "create" category still uses FTS5 fallback.
        assert "existing" in result or "matches" in result


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


def test_orient_default_budget_is_2500():
    """Bumped budget: single source of truth for the size cap."""
    from winkers.mcp.tools import MAX_ORIENT_TOKENS
    assert MAX_ORIENT_TOKENS == 2500


def test_orient_compacts_rules_when_over_budget(graph, tmp_path):
    """When rules_list doesn't fit, return titles-only variant instead of skipping."""
    # Seed a rules.json with rich wrong_approach text so the full section is
    # clearly over a tight budget, but titles alone fit.
    from winkers.conventions import ConventionRule, RulesFile, RulesStore
    rules = RulesFile(rules=[
        ConventionRule(
            id=i, category="style", title=f"Rule {i}",
            content="Lorem ipsum dolor sit amet " * 10,
            wrong_approach="Do not do the thing in the detailed prohibitive sentence" * 5,
            source="manual",
            created="2026-04-15",
        )
        for i in range(1, 6)
    ])
    RulesStore(tmp_path).save(rules)

    # Request map + rules_list. Tight budget: map fits in priority order,
    # rules_list full form would overflow, compact (titles-only) fits.
    result = _tool_orient(
        graph,
        {"include": ["map", "rules_list"], "max_tokens": 250},
        tmp_path,
    )
    # rules_list survives but as compact-only.
    assert "rules_list" in result
    rules_out = result["rules_list"]
    assert rules_out.get("_compacted")
    # Compacted entries contain title but not wrong_approach.
    for _cat, entries in rules_out["categories"].items():
        for entry in entries:
            assert "title" in entry
            assert "wrong_approach" not in entry
    assert result.get("_truncated") is True
    assert "Compacted" in result["_hint"]


# ---------------------------------------------------------------------------
# _files_block — prod / test split for locked_fns (Issue #4 fix)
# ---------------------------------------------------------------------------

def test_files_block_excludes_test_callers_from_locked_fns(graph, tmp_path):
    """A prod function whose only callers live in tests/ is not counted as locked.

    Pytest-fixture injection creates spurious call_edges that used to inflate
    `locked_fns` on medium-sized projects (Issue #4 in the benchmark report).
    """
    from winkers.mcp.tools import _files_block
    from winkers.models import CallEdge, CallSite, FileNode, FunctionNode

    # Clone the graph so other tests aren't affected.
    g = graph.model_copy(deep=True)
    # Inject a prod file + fn with ONLY a test caller.
    prod_path = "modules/lonely.py"
    prod_fn = "modules/lonely.py::only_tests_call_me"
    g.files[prod_path] = FileNode(
        path=prod_path, language="python", imports=[],
        function_ids=[prod_fn],
    )
    g.functions[prod_fn] = FunctionNode(
        id=prod_fn, file=prod_path, name="only_tests_call_me",
        kind="function", language="python",
        line_start=1, line_end=1, params=[],
    )
    g.call_edges.append(CallEdge(
        source_fn="tests/test_lonely.py::test_only_tests_call_me",
        target_fn=prod_fn,
        call_site=CallSite(
            caller_fn_id="tests/test_lonely.py::test_only_tests_call_me",
            file="tests/test_lonely.py", line=5,
            expression="only_tests_call_me()",
        ),
    ))

    block = _files_block(g, [prod_path])
    # Prod fn with only a test caller → not counted as locked.
    assert block["locked_fns"] == 0


# ---------------------------------------------------------------------------
# browse — mid-level function inventory
# ---------------------------------------------------------------------------

def test_browse_lists_all_functions(graph):
    """Default call returns every function as a compact string."""
    result = _tool_browse(graph, {})
    assert result["total"] == len(graph.functions)
    assert result["shown"] <= 50
    assert isinstance(result["functions"], list)
    # Every entry is a string starting with "file::fn_name".
    for line in result["functions"]:
        assert isinstance(line, str)
        assert "::" in line


def test_browse_entry_format_with_intent(graph):
    """Entries with an LLM intent render as 'fid (callers) — intent'."""
    fn = graph.functions["modules/pricing.py::calculate_price"]
    saved = fn.intent
    fn.intent = "computes final price for an item"
    try:
        result = _tool_browse(graph, {"file": "modules/pricing.py"})
        line = next(
            line for line in result["functions"]
            if line.startswith("modules/pricing.py::calculate_price")
        )
        assert " — computes final price for an item" in line
        # Callers count in parens, directly after fn_id.
        assert "modules/pricing.py::calculate_price (" in line
    finally:
        fn.intent = saved


def test_browse_entry_format_without_intent(graph):
    """Entries without intent omit the em-dash suffix — shown only as 'fid (callers)'."""
    fn = graph.functions["modules/pricing.py::calculate_price"]
    saved = fn.intent
    fn.intent = None
    try:
        result = _tool_browse(graph, {"file": "modules/pricing.py"})
        line = next(
            line for line in result["functions"]
            if line.startswith("modules/pricing.py::calculate_price")
        )
        # No em-dash, no trailing intent text.
        assert " — " not in line
        assert line.endswith(")")
    finally:
        fn.intent = saved


def test_browse_zone_filter(graph):
    """zone filter restricts results to one zone."""
    result = _tool_browse(graph, {"zone": "modules"})
    assert result["zone"] == "modules"
    # Every returned fn must live under modules/.
    for line in result["functions"]:
        fid = line.split(" ", 1)[0]
        file = fid.split("::", 1)[0]
        assert graph.file_zone(file) == "modules"


def test_browse_file_filter(graph):
    """file filter restricts results to one file."""
    result = _tool_browse(graph, {"file": "modules/pricing.py"})
    assert result["file"] == "modules/pricing.py"
    # Entries come in two flavours now: fn lines (start with file::) and
    # caller lines (start with "  ← "). Both are expected under file=.
    for line in result["functions"]:
        assert (
            line.startswith("modules/pricing.py::") or line.startswith("  ← ")
        )


def test_browse_file_inlines_caller_call_sites(graph):
    """Under file= the output interleaves '  ← file:line  expression' lines
    after each function. `shown` still counts functions (not caller lines)
    so pagination stays predictable."""
    result = _tool_browse(graph, {"file": "modules/pricing.py"})
    # calculate_price has 2 callers in the fixture.
    fns = [line for line in result["functions"] if not line.startswith("  ← ")]
    callers = [line for line in result["functions"] if line.startswith("  ← ")]
    assert "shown" in result
    assert result["shown"] == len(fns)
    # At least one caller line present (calculate_price is locked).
    assert any("calculate_price(item_id, qty)" in line for line in callers)
    # Caller line format: '  ← <file>:<line>  <expression>'
    for line in callers:
        assert ":" in line  # file:line separator
    # Fn lines stay compact (no callers inlined in themselves).
    for line in fns:
        assert " ← " not in line


def test_browse_zone_does_not_inline_call_sites(graph):
    """Caller-line inlining is a file=-only feature — zone browse stays compact."""
    result = _tool_browse(graph, {"zone": "modules"})
    for line in result["functions"]:
        assert not line.startswith("  ← ")


# ---------------------------------------------------------------------------
# Route info surfacing in per-fn views (scope / browse / hotspots / before_create)
# ---------------------------------------------------------------------------

FLASK_FIXTURE = Path(__file__).parent / "fixtures" / "flask_project"


@pytest.fixture(scope="module")
def flask_graph():
    g = GraphBuilder().build(FLASK_FIXTURE)
    CrossFileResolver().resolve(g, str(FLASK_FIXTURE))
    return g


def test_scope_function_surfaces_route(flask_graph):
    """scope(function=) on a handler includes route / http_method / template."""
    result = _tool_scope(
        flask_graph, {"function": "app.py::product_list"},
    )
    fn_entry = result["function"]
    assert fn_entry.get("route") == "/products"
    assert fn_entry.get("http_method") == "GET"
    assert fn_entry.get("template") == "products/list.html"


def test_scope_file_entries_surface_route(flask_graph):
    """scope(file=) functions[] entries include route when the fn is a handler."""
    result = _tool_scope(flask_graph, {"file": "app.py"})
    product_create = next(
        e for e in result["functions"] if e["name"] == "product_create"
    )
    assert product_create.get("route") == "/products"
    assert product_create.get("http_method") == "POST"


def test_browse_inlines_route_marker(flask_graph):
    """browse prefixes handler lines with '[METHOD /path]' after the (callers) count."""
    result = _tool_browse(flask_graph, {"file": "app.py"})
    index_line = next(
        line for line in result["functions"] if "::index" in line
    )
    assert "[GET /]" in index_line
    post_line = next(
        line for line in result["functions"] if "::product_create" in line
    )
    assert "[POST /products]" in post_line


def test_browse_skips_route_marker_for_non_handlers(graph):
    """Plain fns (no @route decorator) don't get a '[...]' marker."""
    result = _tool_browse(graph, {"file": "modules/pricing.py"})
    for line in result["functions"]:
        if line.startswith("  ← "):
            continue
        # No marker for business-logic functions in the non-web fixture.
        assert "[GET " not in line and "[POST " not in line


def test_hotspots_include_route(flask_graph, tmp_path):
    """orient.hotspots adds route/http_method on handler entries."""
    from winkers.mcp.tools import _section_hotspots
    section = _section_hotspots(flask_graph, min_callers=0, root=tmp_path)
    hotspots = section["hotspots"]
    index_entry = next(h for h in hotspots if "index" in h["function"])
    assert index_entry.get("route") == "/"
    assert index_entry.get("http_method") == "GET"


def test_before_create_affected_fns_include_route(flask_graph, tmp_path):
    """affected_fns carry route info so agents see HTTP context inline."""
    result = _tool_before_create(
        flask_graph,
        {"intent": "change product_list() response format"},
        tmp_path,
    )
    affected = result["functions"]["affected_fns"]
    entry = next(e for e in affected if e["name"] == "product_list")
    assert entry.get("route") == "/products"
    assert entry.get("http_method") == "GET"


def test_browse_min_callers_filter(graph):
    """min_callers hides functions with fewer callers than threshold."""
    result = _tool_browse(graph, {"min_callers": 1})
    for line in result["functions"]:
        fid = line.split(" ", 1)[0]
        assert len(graph.callers(fid)) >= 1


def test_browse_pagination(graph):
    """offset/limit paginate; next_offset appears while more items remain."""
    first = _tool_browse(graph, {"limit": 2, "offset": 0})
    assert first["shown"] == 2
    if first["total"] > 2:
        assert first.get("next_offset") == 2
        second = _tool_browse(graph, {"limit": 2, "offset": 2})
        assert second["offset"] == 2
        # No overlap with first page.
        assert set(first["functions"]).isdisjoint(set(second["functions"]))
    else:
        assert "next_offset" not in first


def test_browse_limit_clamped_to_max(graph):
    """limit above 100 clamps silently to 100 (no error)."""
    result = _tool_browse(graph, {"limit": 500})
    assert result["shown"] <= 100


def test_browse_empty_match_returns_hint(graph):
    """When filters exclude everything, include a hint instead of silent empty."""
    result = _tool_browse(graph, {"zone": "nonexistent-zone"})
    assert result["total"] == 0
    assert result["functions"] == []
    assert "hint" in result
    # Hint echoes the filter so the agent sees what to change.
    assert "nonexistent-zone" in result["hint"]
    # No zone files existed under that name → no files_in_zone fallback.
    assert "files_in_zone" not in result


def test_browse_empty_zone_surfaces_file_list(graph):
    """When zone matches real files but yields 0 functions (e.g. all fns
    filtered out by some other mechanism, or zone label exists but fn.intent
    is just unpopulated and min_callers cuts everything out), the response
    lists the files so the agent can drill down with browse(file=...)."""
    from winkers.models import FileNode

    g = graph.model_copy(deep=True)
    # Ensure an "empty" zone: add a file with no functions under a subdir.
    g.files["app/empty/placeholder.py"] = FileNode(
        path="app/empty/placeholder.py", language="python", imports=[],
        function_ids=[], zone="app",
    )
    g.files["app/empty/other.py"] = FileNode(
        path="app/empty/other.py", language="python", imports=[],
        function_ids=[], zone="app",
    )
    result = _tool_browse(g, {"zone": "app/empty"})
    assert result["total"] == 0
    assert "files_in_zone" in result
    assert "app/empty/placeholder.py" in result["files_in_zone"]
    assert "app/empty/other.py" in result["files_in_zone"]
    # Hint should point at browse(file=) as the next step.
    assert "browse(file=" in result["hint"]


def test_browse_zone_accepts_subdirectory_path(graph):
    """Issue #2: browse(zone='sub/dir') matches files under that prefix,
    even when graph stores only a top-level zone label."""
    from winkers.models import FileNode, FunctionNode

    g = graph.model_copy(deep=True)
    # Simulate invoicekit shape: all under zone="app", subdir names are paths.
    g.files["app/services/invoice.py"] = FileNode(
        path="app/services/invoice.py", language="python", imports=[],
        function_ids=["app/services/invoice.py::create_invoice"],
        zone="app",
    )
    g.functions["app/services/invoice.py::create_invoice"] = FunctionNode(
        id="app/services/invoice.py::create_invoice",
        file="app/services/invoice.py", name="create_invoice",
        kind="function", language="python",
        line_start=1, line_end=1, params=[],
    )
    g.files["app/repos/invoice.py"] = FileNode(
        path="app/repos/invoice.py", language="python", imports=[],
        function_ids=["app/repos/invoice.py::get_with_items"],
        zone="app",
    )
    g.functions["app/repos/invoice.py::get_with_items"] = FunctionNode(
        id="app/repos/invoice.py::get_with_items",
        file="app/repos/invoice.py", name="get_with_items",
        kind="function", language="python",
        line_start=1, line_end=1, params=[],
    )

    result = _tool_browse(g, {"zone": "app/services"})
    # Only the services/ file matches, even though both files carry zone="app".
    ids = {line.split(" ", 1)[0] for line in result["functions"]}
    assert "app/services/invoice.py::create_invoice" in ids
    assert "app/repos/invoice.py::get_with_items" not in ids


def test_browse_zone_still_matches_label(graph):
    """Backward compat: browse(zone='modules') still works as a plain label match."""
    result = _tool_browse(graph, {"zone": "modules"})
    assert result["total"] > 0
    for line in result["functions"]:
        fid = line.split(" ", 1)[0]
        file = fid.split("::", 1)[0]
        # All results must be under modules/ (either via label or prefix).
        assert graph.file_zone(file) == "modules" or file.startswith("modules/")


def test_browse_int_params_accept_strings(graph):
    """Issue #3: handler coerces string integers so MCP clients that stringify
    args don't silently lose the call. Schema-level fix exercised indirectly
    via the handler's int() coercion."""
    result = _tool_browse(
        graph, {"min_callers": "1", "limit": "3", "offset": "0"}
    )
    assert result["shown"] <= 3
    assert result["min_callers"] == 1


def test_orient_rules_list_survives_budget_starvation(graph, tmp_path):
    """Issue #5: rules_list is not silently dropped when map/conventions eat
    most of the budget — a reserved floor keeps the compact form visible."""
    from winkers.conventions import ConventionRule, RulesFile, RulesStore
    rules = RulesFile(rules=[
        ConventionRule(
            id=i, category="style", title=f"Rule {i}",
            content="Lorem ipsum " * 20,
            wrong_approach="Do not do the thing " * 20,
            source="manual",
            created="2026-04-16",
        )
        for i in range(1, 8)
    ])
    RulesStore(tmp_path).save(rules)

    # Tight budget where full map+conventions+rules_list would overflow,
    # but the compact-rules reserve should guarantee it survives.
    result = _tool_orient(
        graph,
        {"include": ["map", "conventions", "rules_list"], "max_tokens": 700},
        tmp_path,
    )
    assert "rules_list" in result
    # Non-empty categories — guard against the old non-None-but-empty bug.
    rl = result["rules_list"]
    assert rl.get("categories"), (
        f"rules_list must surface categories, got {rl!r}"
    )


def test_try_compact_returns_none_for_empty_categories():
    """Issue #5 (part 1): _try_compact returns None (skip) when there's nothing
    meaningful to show, rather than a non-None dict with empty categories."""
    from winkers.mcp.tools import _try_compact
    # Empty categories → skip.
    assert _try_compact("rules_list", {"total": 0, "categories": {}}) is None
    # Missing categories → skip.
    assert _try_compact("rules_list", {"total": 0}) is None
    # Rules_list with real content → compact variant returned.
    compact = _try_compact("rules_list", {
        "total": 1,
        "categories": {
            "style": [{"id": 1, "title": "A", "wrong_approach": "avoid this"}]
        },
    })
    assert compact is not None
    assert compact["categories"]["style"] == [{"id": 1, "title": "A"}]


# ---------------------------------------------------------------------------
# _files_block — direct_caller_files / importing_files (migration_cost split)
# ---------------------------------------------------------------------------

def test_files_block_exposes_importing_files_count(graph, tmp_path):
    """importing_files = unique external files that import from the target.

    Always ≤ migration_cost (which counts raw import edges, not unique files).
    """
    from winkers.mcp.tools import _files_block
    block = _files_block(graph, ["modules/pricing.py"])
    # api/prices.py and modules/inventory.py import from pricing.
    assert block["importing_files"] == len(block["imported_by"])
    # migration_cost can equal or exceed importing_files.
    assert block["migration_cost"] >= block["importing_files"]


def test_files_block_direct_caller_files_fn_level(graph, tmp_path):
    """When explicit_fns names a specific fn, direct_caller_files lists the
    ACTUAL call-site files — the tight hands-on editing surface."""
    from winkers.mcp.tools import _files_block

    fn_id = "modules/pricing.py::calculate_price"
    block = _files_block(graph, ["modules/pricing.py"], explicit_fns={fn_id})

    # calculate_price is called from api/prices.py and modules/inventory.py.
    assert "direct_caller_files" in block
    assert set(block["direct_caller_files"]) == {
        "api/prices.py", "modules/inventory.py",
    }
    assert block["direct_caller_files_count"] == 2


def test_files_block_direct_caller_files_absent_without_explicit_fns(graph, tmp_path):
    """Without explicit_fns the direct-caller surface is not computed — the
    agent falls back to the importer-level importing_files / migration_cost."""
    from winkers.mcp.tools import _files_block
    block = _files_block(graph, ["modules/pricing.py"])
    assert "direct_caller_files" not in block
    assert "direct_caller_files_count" not in block
    assert "importing_files" in block  # importer-level is still there


def test_files_block_direct_caller_narrower_than_importing(graph):
    """Demonstrates why the split matters: importer may not actually call
    the specific fn we're changing.

    Inject a file that imports from pricing.py but never calls
    calculate_price — it inflates importing_files but should NOT appear in
    direct_caller_files for a fn-level intent on calculate_price."""
    from winkers.mcp.tools import _files_block
    from winkers.models import FileNode, FunctionNode, ImportEdge

    g = graph.model_copy(deep=True)
    # Phantom importer: imports the module but only touches get_base_price.
    g.files["api/debug.py"] = FileNode(
        path="api/debug.py", language="python", imports=[],
        function_ids=["api/debug.py::dump_base"],
    )
    g.functions["api/debug.py::dump_base"] = FunctionNode(
        id="api/debug.py::dump_base", file="api/debug.py", name="dump_base",
        kind="function", language="python", line_start=1, line_end=1, params=[],
    )
    g.import_edges.append(ImportEdge(
        source_file="api/debug.py",
        target_file="modules/pricing.py",
        names=["get_base_price"],
    ))
    # No call edge from dump_base to calculate_price — only imports the module.

    fn_id = "modules/pricing.py::calculate_price"
    block = _files_block(g, ["modules/pricing.py"], explicit_fns={fn_id})

    assert "api/debug.py" in block["imported_by"]
    assert block["importing_files"] >= 3
    # But the phantom importer doesn't call calculate_price → narrower surface.
    assert "api/debug.py" not in block["direct_caller_files"]
    assert block["direct_caller_files_count"] < block["importing_files"]


def test_files_block_counts_test_fixtures_separately(graph, tmp_path):
    """Locked functions inside tests/ files increment `locked_test_fns`, not `locked_fns`."""
    from winkers.mcp.tools import _files_block
    from winkers.models import CallEdge, CallSite, FileNode, FunctionNode

    g = graph.model_copy(deep=True)
    test_path = "tests/conftest.py"
    fixture_fn = "tests/conftest.py::client_fixture"
    user_fn = "tests/test_api.py::test_login"
    g.files[test_path] = FileNode(
        path=test_path, language="python", imports=[],
        function_ids=[fixture_fn],
    )
    g.functions[fixture_fn] = FunctionNode(
        id=fixture_fn, file=test_path, name="client_fixture",
        kind="function", language="python",
        line_start=1, line_end=1, params=[],
    )
    # Another test "calls" the fixture (pytest-fixture-injection style).
    g.call_edges.append(CallEdge(
        source_fn=user_fn, target_fn=fixture_fn,
        call_site=CallSite(
            caller_fn_id=user_fn, file="tests/test_api.py", line=3,
            expression="client_fixture",
        ),
    ))

    block = _files_block(g, [test_path])
    assert block["locked_fns"] == 0
    assert block.get("locked_test_fns") == 1


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
