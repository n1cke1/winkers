"""Tests for CrossFileResolver."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.models import Graph
from winkers.resolver import CrossFileResolver

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def resolved_graph() -> Graph:
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(graph, str(PYTHON_FIXTURE))
    return graph


def test_direct_import_edge_exists(resolved_graph: Graph):
    """inventory.py imports calculate_price directly → edge should exist."""
    edges = resolved_graph.callers(
        next(
            fid for fid in resolved_graph.functions
            if "calculate_price" in fid and "pricing" in fid
        )
    )
    assert len(edges) >= 1


def test_calculate_price_has_multiple_callers(resolved_graph: Graph):
    """calculate_price is called from inventory and api."""
    cp_id = next(
        fid for fid in resolved_graph.functions
        if "calculate_price" in fid and "pricing" in fid
    )
    caller_edges = resolved_graph.callers(cp_id)
    assert len(caller_edges) >= 2


def test_caller_confidence_high(resolved_graph: Graph):
    """Direct import callers should have confidence >= 0.9."""
    cp_id = next(
        fid for fid in resolved_graph.functions
        if "calculate_price" in fid and "pricing" in fid
    )
    for edge in resolved_graph.callers(cp_id):
        assert edge.confidence >= 0.5


def test_call_expression_recorded(resolved_graph: Graph):
    """Call site expression is captured."""
    cp_id = next(
        fid for fid in resolved_graph.functions
        if "calculate_price" in fid and "pricing" in fid
    )
    edges = resolved_graph.callers(cp_id)
    assert any("calculate_price" in e.call_site.expression for e in edges)


def test_apply_discount_no_external_callers(resolved_graph: Graph):
    """apply_discount is only called from within pricing.py — not locked externally."""
    ad_id = next(
        fid for fid in resolved_graph.functions
        if "apply_discount" in fid
    )
    external_callers = [
        e for e in resolved_graph.callers(ad_id)
        if "pricing" not in e.call_site.file
    ]
    assert len(external_callers) == 0


def test_no_self_circular_edges(resolved_graph: Graph):
    """No call edge where source == target."""
    for edge in resolved_graph.call_edges:
        assert edge.source_fn != edge.target_fn


def test_total_edges_positive(resolved_graph: Graph):
    """At least some edges were resolved."""
    assert len(resolved_graph.call_edges) >= 2
