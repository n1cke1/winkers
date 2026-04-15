"""Unit tests for winkers.target_resolution — intent categorization + regex target resolution."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.target_resolution import (
    categorize_intent,
    extract_explicit_targets,
    is_test_path,
    resolve_targets,
)

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# categorize_intent
# ---------------------------------------------------------------------------

def test_categorize_create_keywords():
    assert categorize_intent("add a new pricing function") == "create"
    assert categorize_intent("implement batch discounts") == "create"
    assert categorize_intent("build a new endpoint") == "create"


def test_categorize_change_structural_keywords():
    """Structural verbs (move/merge/consolidate/split) → change."""
    assert categorize_intent("consolidate domain/ files") == "change"
    assert categorize_intent("move helpers into utils.py") == "change"
    assert categorize_intent("split pricing into two modules") == "change"
    assert categorize_intent("merge tax and discount into rules") == "change"


def test_categorize_change_inplace_keywords():
    """In-place edit verbs (refactor/simplify/rename/remove) → change."""
    assert categorize_intent("simplify calculate_price") == "change"
    assert categorize_intent("rename get_price to fetch_price") == "change"
    assert categorize_intent("refactor the inventory module") == "change"
    assert categorize_intent("remove deprecated helper") == "change"


def test_categorize_change_outranks_create():
    """'add a new module by consolidating X' should read as change."""
    assert categorize_intent("add a new module by consolidating X") == "change"


def test_categorize_empty_or_unknown():
    assert categorize_intent("") == "unknown"
    assert categorize_intent("something vague happens here") == "unknown"


# ---------------------------------------------------------------------------
# resolve_targets
# ---------------------------------------------------------------------------

def test_resolve_targets_by_zone(graph):
    """Zone name 'modules' should resolve to every file in that zone."""
    t = resolve_targets("consolidate modules/ files", graph)
    assert "modules" in t.zones
    assert any("pricing.py" in p for p in t.paths)
    assert any("inventory.py" in p for p in t.paths)


def test_resolve_targets_by_file_basename(graph):
    """Basename match should pick up the file path."""
    t = resolve_targets("rename calculate_price in pricing.py", graph)
    assert any(p.endswith("pricing.py") for p in t.paths)


def test_resolve_targets_by_function_name(graph):
    """Function name ≥4 chars should be picked up."""
    t = resolve_targets("simplify calculate_price", graph)
    assert any("calculate_price" in fid for fid in t.functions)


def test_resolve_targets_empty_for_unrelated(graph):
    """Intent with no recognizable names produces empty targets."""
    t = resolve_targets("clean up the code", graph)
    assert t.is_empty()


def test_resolve_targets_short_names_ignored(graph):
    """Intent with only short tokens (len < 4) does not false-positive."""
    t = resolve_targets("fix id", graph)
    # 'fix' and 'id' are both < 4 chars; nothing in the graph should match.
    assert t.is_empty()


def test_resolve_targets_word_boundary(graph):
    """Function name must be a whole word, not a substring match."""
    # 'calculate' alone should not match 'calculate_price' because the full
    # function name 'calculate_price' is not the intent; but 'calculate' is a
    # prefix, not a whole identifier. Our _contains_word checks whole identifier
    # boundaries — so 'calculate' as a word won't match 'calculate_price'.
    t = resolve_targets("do some calculate thing", graph)
    # No function is literally named 'calculate' in the fixture.
    names_found = {fid.split("::")[-1] for fid in t.functions}
    assert "calculate_price" not in names_found


# ---------------------------------------------------------------------------
# extract_explicit_targets
# ---------------------------------------------------------------------------

def test_extract_explicit_targets_fn_calls():
    """`fn_name()` syntax yields explicit function targets."""
    fns, paths = extract_explicit_targets("rename calculate_price() to compute_price()")
    assert "calculate_price" in fns
    assert "compute_price" in fns
    assert paths == set()


def test_extract_explicit_targets_class_method():
    """`Class.method()` yields the dotted form as an explicit fn."""
    fns, _ = extract_explicit_targets(
        "fix InvoiceRepo.get_with_items() selectinload"
    )
    assert "InvoiceRepo.get_with_items" in fns


def test_extract_explicit_targets_paths_forward_and_back_slash():
    """Paths with forward and back slashes are both captured, normalized to /."""
    fns, paths = extract_explicit_targets(
        "fix error handling in app/repos/invoice.py and in app\\repos\\client.py"
    )
    assert "app/repos/invoice.py" in paths
    assert "app/repos/client.py" in paths
    assert fns == set()


def test_extract_explicit_targets_double_colon():
    """`file.py::fn` and `file.py::Class.method` are parsed."""
    fns, paths = extract_explicit_targets(
        "modify modules/pricing.py::calculate_price and "
        "app/repos/invoice.py::InvoiceRepo.get_with_items"
    )
    assert "modules/pricing.py" in paths
    assert "app/repos/invoice.py" in paths
    assert "calculate_price" in fns
    assert "InvoiceRepo.get_with_items" in fns


def test_extract_explicit_targets_filters_keywords():
    """Language keywords before `(` are not mistaken for function names."""
    fns, _ = extract_explicit_targets(
        "if (ready) return something; else while (x) continue"
    )
    # None of these control-flow words should appear as fn targets.
    for kw in ("if", "return", "while", "else", "continue"):
        assert kw not in fns


def test_extract_explicit_targets_empty_intent():
    """Empty input returns empty sets, not None."""
    fns, paths = extract_explicit_targets("")
    assert fns == set()
    assert paths == set()


# ---------------------------------------------------------------------------
# test-path filter
# ---------------------------------------------------------------------------

def test_is_test_path_various():
    assert is_test_path("tests/test_pricing.py")
    assert is_test_path("app/tests/unit.py")
    assert is_test_path("pkg/test/helpers.py")
    assert is_test_path("pkg\\tests\\win.py")
    assert is_test_path("tests/conftest.py")
    assert not is_test_path("app/repos/invoice.py")
    assert not is_test_path("modules/pricing.py")


def test_resolve_targets_fuzzy_drops_test_paths():
    """Fuzzy path match that hits test/ files is filtered unless intent targets tests."""
    from winkers.graph import GraphBuilder
    from winkers.models import FileNode, FunctionNode

    g = GraphBuilder().build(PYTHON_FIXTURE)
    # Inject a fake test-file entry so that fuzzy matching finds it via basename.
    g.files["tests/test_pricing.py"] = FileNode(
        path="tests/test_pricing.py", language="python", imports=[],
        function_ids=["tests/test_pricing.py::test_calculate_price"],
    )
    g.functions["tests/test_pricing.py::test_calculate_price"] = FunctionNode(
        id="tests/test_pricing.py::test_calculate_price",
        file="tests/test_pricing.py", name="test_calculate_price",
        kind="function", language="python", line_start=1, line_end=1, params=[],
    )
    # Intent mentions calculate_price and pricing.py — no test markers.
    t = resolve_targets("simplify calculate_price in pricing.py", g)
    assert "tests/test_pricing.py" not in t.paths
    assert "tests/test_pricing.py::test_calculate_price" not in t.functions


def test_resolve_targets_includes_tests_when_intent_path_like_markers():
    """Intent with `tests/` or `test_` path-like tokens includes test files."""
    from winkers.graph import GraphBuilder
    from winkers.models import FileNode, FunctionNode

    g = GraphBuilder().build(PYTHON_FIXTURE)
    g.files["tests/test_pricing.py"] = FileNode(
        path="tests/test_pricing.py", language="python", imports=[],
        function_ids=["tests/test_pricing.py::test_calculate_price"],
    )
    g.functions["tests/test_pricing.py::test_calculate_price"] = FunctionNode(
        id="tests/test_pricing.py::test_calculate_price",
        file="tests/test_pricing.py", name="test_calculate_price",
        kind="function", language="python", line_start=1, line_end=1, params=[],
    )
    t = resolve_targets("update tests/test_pricing.py after rename", g)
    assert "tests/test_pricing.py" in t.paths


def test_resolve_targets_bare_add_tests_does_not_pull_tests():
    """`add tests for X` is not a path-like marker — tests still filtered.

    This is the core Issue #4 fix: fuzzy match on "X" finds prod files
    AND test files, but test files are dropped.
    """
    from winkers.graph import GraphBuilder
    from winkers.models import FileNode, FunctionNode

    g = GraphBuilder().build(PYTHON_FIXTURE)
    g.files["tests/test_pricing.py"] = FileNode(
        path="tests/test_pricing.py", language="python", imports=[],
        function_ids=["tests/test_pricing.py::test_calculate_price"],
    )
    g.functions["tests/test_pricing.py::test_calculate_price"] = FunctionNode(
        id="tests/test_pricing.py::test_calculate_price",
        file="tests/test_pricing.py", name="test_calculate_price",
        kind="function", language="python", line_start=1, line_end=1, params=[],
    )
    t = resolve_targets("add comprehensive tests for calculate_price", g)
    # Bare "tests" is not path-like — test files are still filtered.
    assert "tests/test_pricing.py" not in t.paths


def test_resolve_targets_explicit_fn_filters_tests(graph):
    """Explicit `fn()` resolves to prod fn only, even when a same-named test fn exists."""
    from winkers.models import FunctionNode
    # Re-use the fixture graph; inject a homonymous test function.
    graph.functions["tests/test_pricing.py::calculate_price"] = FunctionNode(
        id="tests/test_pricing.py::calculate_price",
        file="tests/test_pricing.py", name="calculate_price",
        kind="function", language="python", line_start=1, line_end=1, params=[],
    )
    try:
        t = resolve_targets("refactor calculate_price() to round half-even", graph)
        assert "tests/test_pricing.py::calculate_price" not in t.functions
        assert "modules/pricing.py::calculate_price" in t.functions
    finally:
        graph.functions.pop("tests/test_pricing.py::calculate_price", None)
