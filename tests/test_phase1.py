"""Phase 1 integration tests — full pipeline on fixtures."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from winkers.cli.main import cli
from winkers.graph import GraphBuilder
from winkers.models import Graph
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"
TS_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_project"


# ---------------------------------------------------------------------------
# Python project — full pipeline
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def py_graph() -> Graph:
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(graph, str(PYTHON_FIXTURE))
    return graph


def test_python_files_found(py_graph: Graph):
    assert len(py_graph.files) >= 4  # modules/pricing, modules/inventory, api/prices, models


def test_python_functions_found(py_graph: Graph):
    assert len(py_graph.functions) >= 7


def test_calculate_price_is_locked(py_graph: Graph):
    cp_id = next(
        fid for fid in py_graph.functions if "calculate_price" in fid and "pricing" in fid
    )
    assert py_graph.is_locked(cp_id)


def test_calculate_price_params(py_graph: Graph):
    cp_id = next(
        fid for fid in py_graph.functions if "calculate_price" in fid and "pricing" in fid
    )
    fn = py_graph.functions[cp_id]
    param_names = [p.name for p in fn.params]
    assert "item_id" in param_names
    assert "qty" in param_names


def test_calculate_price_return_type(py_graph: Graph):
    cp_id = next(
        fid for fid in py_graph.functions if "calculate_price" in fid and "pricing" in fid
    )
    fn = py_graph.functions[cp_id]
    assert fn.return_type == "float"


def test_calculate_price_has_callers(py_graph: Graph):
    cp_id = next(
        fid for fid in py_graph.functions if "calculate_price" in fid and "pricing" in fid
    )
    assert len(py_graph.callers(cp_id)) >= 2


def test_apply_discount_not_locked(py_graph: Graph):
    ad_id = next(
        fid for fid in py_graph.functions if "apply_discount" in fid
    )
    # apply_discount may be called internally from calculate_price, but not from other files
    external = [
        e for e in py_graph.callers(ad_id)
        if "pricing" not in e.call_site.file
    ]
    assert len(external) == 0


def test_edges_have_confidence(py_graph: Graph):
    assert all(e.confidence > 0 for e in py_graph.call_edges)


def test_call_expression_non_empty(py_graph: Graph):
    assert all(e.call_site.expression for e in py_graph.call_edges)


def test_meta_populated(py_graph: Graph):
    assert "python" in py_graph.meta.get("languages", [])
    assert py_graph.meta["total_functions"] >= 7


# ---------------------------------------------------------------------------
# TypeScript project — full pipeline
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ts_graph() -> Graph:
    graph = GraphBuilder().build(TS_FIXTURE)
    CrossFileResolver().resolve(graph, str(TS_FIXTURE))
    return graph


def test_ts_files_found(ts_graph: Graph):
    assert len(ts_graph.files) >= 3


def test_ts_calculate_price_found(ts_graph: Graph):
    names = [fn.name for fn in ts_graph.functions.values()]
    assert "calculatePrice" in names


def test_ts_calculate_price_is_locked(ts_graph: Graph):
    cp_id = next(fid for fid in ts_graph.functions if "calculatePrice" in fid)
    assert ts_graph.is_locked(cp_id)


# ---------------------------------------------------------------------------
# Store round-trip
# ---------------------------------------------------------------------------

def test_store_roundtrip(tmp_path: Path):
    graph = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(graph, str(PYTHON_FIXTURE))
    store = GraphStore(tmp_path)
    store.save(graph)
    loaded = store.load()
    assert loaded is not None
    assert loaded.model_dump() == graph.model_dump()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

@pytest.fixture()
def project_copy(tmp_path: Path) -> Path:
    shutil.copytree(PYTHON_FIXTURE, tmp_path / "project")
    return tmp_path / "project"


def test_cli_init(project_copy: Path):
    runner = CliRunner()
    init_result = runner.invoke(cli, ["init", "--no-semantic", str(project_copy)])
    assert init_result.exit_code == 0
    assert (project_copy / ".winkers" / "graph.json").exists()
