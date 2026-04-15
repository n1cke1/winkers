"""Unit tests for winkers.target_resolution — intent categorization + regex target resolution."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.target_resolution import (
    categorize_intent,
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
