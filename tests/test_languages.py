"""Tests for Java, Go, Rust, C# language profiles."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.models import Graph
from winkers.resolver import CrossFileResolver

FIXTURES = Path(__file__).parent / "fixtures"


def _build(lang: str) -> Graph:
    g = GraphBuilder().build(FIXTURES / f"{lang}_project")
    CrossFileResolver().resolve(g, str(FIXTURES / f"{lang}_project"))
    return g


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def java_graph() -> Graph:
    return _build("java")


def test_java_files_found(java_graph):
    assert len(java_graph.files) == 2


def test_java_functions_found(java_graph):
    names = [fn.name for fn in java_graph.functions.values()]
    assert "calculatePrice" in names
    assert "applyDiscount" in names


def test_java_calculate_price_locked(java_graph):
    fn_id = next(fid for fid in java_graph.functions if "calculatePrice" in fid)
    assert java_graph.is_locked(fn_id)


def test_java_reserve_items_free(java_graph):
    fn_id = next(fid for fid in java_graph.functions if "reserveItems" in fid)
    assert not java_graph.is_locked(fn_id)


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def go_graph() -> Graph:
    return _build("go")


def test_go_files_found(go_graph):
    assert len(go_graph.files) == 2


def test_go_functions_found(go_graph):
    names = [fn.name for fn in go_graph.functions.values()]
    assert "CalculatePrice" in names
    assert "applyDiscount" in names


def test_go_exported_uppercase(go_graph):
    from winkers.languages.go import GoProfile
    profile = GoProfile()
    assert profile.is_exported("CalculatePrice", []) is True
    assert profile.is_exported("applyDiscount", []) is False


def test_go_calculate_price_locked(go_graph):
    fn_id = next(fid for fid in go_graph.functions if "CalculatePrice" in fid)
    assert go_graph.is_locked(fn_id)


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rust_graph() -> Graph:
    return _build("rust")


def test_rust_files_found(rust_graph):
    assert len(rust_graph.files) == 2


def test_rust_functions_found(rust_graph):
    names = [fn.name for fn in rust_graph.functions.values()]
    assert "calculate_price" in names
    assert "apply_discount" in names


def test_rust_calculate_price_locked(rust_graph):
    fn_id = next(fid for fid in rust_graph.functions if "calculate_price" in fid)
    assert rust_graph.is_locked(fn_id)


def test_rust_reserve_items_free(rust_graph):
    fn_id = next(fid for fid in rust_graph.functions if "reserve_items" in fid)
    assert not rust_graph.is_locked(fn_id)


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def csharp_graph() -> Graph:
    return _build("csharp")


def test_csharp_files_found(csharp_graph):
    assert len(csharp_graph.files) == 2


def test_csharp_functions_found(csharp_graph):
    names = [fn.name for fn in csharp_graph.functions.values()]
    assert "CalculatePrice" in names
    assert "ApplyDiscount" in names


def test_csharp_calculate_price_locked(csharp_graph):
    fn_id = next(fid for fid in csharp_graph.functions if "CalculatePrice" in fid)
    assert csharp_graph.is_locked(fn_id)


def test_csharp_reserve_items_free(csharp_graph):
    fn_id = next(fid for fid in csharp_graph.functions if "ReserveItems" in fid)
    assert not csharp_graph.is_locked(fn_id)
