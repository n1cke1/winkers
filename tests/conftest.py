"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.models import Graph
from winkers.resolver import CrossFileResolver

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"
TS_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_project"


@pytest.fixture(scope="session")
def python_graph() -> Graph:
    """Built graph for the python_project fixture."""
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(graph, str(PYTHON_FIXTURE))
    return graph


@pytest.fixture(scope="session")
def ts_graph() -> Graph:
    """Built graph for the typescript_project fixture."""
    graph = GraphBuilder().build(TS_FIXTURE)
    CrossFileResolver().resolve(graph, str(TS_FIXTURE))
    return graph
