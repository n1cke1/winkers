"""Heuristic self.<attr>.method() resolver (Task 2)."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.models import Graph
from winkers.resolver import CrossFileResolver

OOP_FIXTURE = Path(__file__).parent / "fixtures" / "python_oop"


@pytest.fixture(scope="module")
def oop_graph() -> Graph:
    graph = GraphBuilder().build(OOP_FIXTURE)
    CrossFileResolver().resolve(graph, str(OOP_FIXTURE))
    return graph


def test_class_metadata_populated(oop_graph: Graph):
    """Graph records class → file mapping for every class defined."""
    assert oop_graph.class_files.get("AuditLogRepo") == "repos/audit_log.py"
    assert oop_graph.class_files.get("ClientRepo") == "repos/client.py"
    assert oop_graph.class_files.get("AuditService") == "services/audit.py"


def test_init_attr_types_recorded(oop_graph: Graph):
    """`self.X = ClassName(...)` in __init__ lands in class_attr_types."""
    svc_attrs = oop_graph.class_attr_types.get("AuditService", {})
    assert svc_attrs.get("audit_repo") == "AuditLogRepo"
    assert svc_attrs.get("client_repo") == "ClientRepo"
    # DI-style self.x = arg must NOT appear (out of scope).
    assert "plain_attr" not in svc_attrs


def test_method_functions_have_class_name(oop_graph: Graph):
    """Methods carry class_name; free functions do not."""
    create_fn = oop_graph.functions["repos/audit_log.py::create"]
    assert create_fn.class_name == "AuditLogRepo"
    assert create_fn.kind == "method"


def test_self_attr_edges_created(oop_graph: Graph):
    """`self.audit_repo.create(...)` in AuditService resolves to AuditLogRepo.create."""
    create_id = "repos/audit_log.py::create"
    callers = oop_graph.callers(create_id)
    caller_ids = {e.source_fn for e in callers}
    assert "services/audit.py::log_event" in caller_ids


def test_self_attr_edge_confidence(oop_graph: Graph):
    """Heuristic edges carry the dedicated 0.85 confidence."""
    edges = oop_graph.callers("repos/audit_log.py::get_by_id")
    assert any(
        e.source_fn == "services/audit.py::log_event" and e.confidence == 0.85
        for e in edges
    )


def test_multiple_repo_methods_resolved(oop_graph: Graph):
    """Both client_repo.create and client_repo.find_by_email get edges."""
    assert any(
        e.source_fn == "services/audit.py::record_client"
        for e in oop_graph.callers("repos/client.py::create")
    )
    assert any(
        e.source_fn == "services/audit.py::record_client"
        for e in oop_graph.callers("repos/client.py::find_by_email")
    )


def test_di_attr_does_not_emit_edge(oop_graph: Graph):
    """`self.plain_attr.commit()` is DI pattern — must NOT produce an edge.

    The attribute was assigned from a constructor param (out of scope for
    MVP). No target method `commit` exists in the graph anyway, but the
    core invariant is that no false edge is emitted for plain_attr.
    """
    for edge in oop_graph.call_edges:
        assert not (
            edge.call_site.expression.startswith("self.plain_attr.")
        ), "DI attribute must not produce heuristic edges"


def test_resolver_counters_in_meta(oop_graph: Graph):
    """Resolver records resolved/skipped counts in meta."""
    assert oop_graph.meta.get("self_attr_resolved", 0) >= 4
    # plain_attr.commit() is an attempted self-attr that must be skipped.
    assert oop_graph.meta.get("self_attr_skipped", 0) >= 1
