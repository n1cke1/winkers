"""Tests for GraphStore."""

from pathlib import Path

from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


def test_save_creates_directory(tmp_path: Path):
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    store = GraphStore(tmp_path)
    store.save(graph)
    assert (tmp_path / ".winkers" / "graph.json").exists()


def test_roundtrip(tmp_path: Path):
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(graph, str(PYTHON_FIXTURE))
    store = GraphStore(tmp_path)
    store.save(graph)
    loaded = store.load()
    assert loaded is not None
    assert loaded.model_dump() == graph.model_dump()


def test_load_nonexistent_returns_none(tmp_path: Path):
    store = GraphStore(tmp_path)
    assert store.load() is None


def test_exists_false_before_save(tmp_path: Path):
    store = GraphStore(tmp_path)
    assert store.exists() is False


def test_exists_true_after_save(tmp_path: Path):
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    store = GraphStore(tmp_path)
    store.save(graph)
    assert store.exists() is True


def test_functions_preserved_after_roundtrip(tmp_path: Path):
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    store = GraphStore(tmp_path)
    store.save(graph)
    loaded = store.load()
    assert loaded is not None
    assert set(loaded.functions.keys()) == set(graph.functions.keys())
