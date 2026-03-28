"""End-to-end tests: init → map → scope → analyze."""

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from winkers.cli.main import cli
from winkers.graph import GraphBuilder
from winkers.mcp.tools import _section_map, _tool_scope
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"
TS_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_project"


# ---------------------------------------------------------------------------
# Full workflow via tool functions directly
# ---------------------------------------------------------------------------

def test_e2e_python_init_map_scope(tmp_path):
    """init → map(zones) → map(files) → scope(fn) all return consistent data."""
    project = tmp_path / "project"
    shutil.copytree(PYTHON_FIXTURE, project)

    # Init
    graph = GraphBuilder().build(project)
    CrossFileResolver().resolve(graph, str(project))
    store = GraphStore(project)
    store.save(graph)

    # map
    map_result = _section_map(graph, None, project)
    assert "zones" in map_result
    assert map_result["total_files"] >= 4
    assert map_result["total_functions"] >= 7
    zone_names = [z["name"] for z in map_result["zones"]]
    assert "modules" in zone_names

    # scope calculate_price
    scope_result = _tool_scope(graph, {"function": "modules/pricing.py::calculate_price"})
    assert scope_result["function"]["locked"] is True
    assert len(scope_result["callers"]) >= 2



def test_e2e_graph_round_trip(tmp_path):
    """Built graph survives save → load with all data intact."""
    project = tmp_path / "project"
    shutil.copytree(PYTHON_FIXTURE, project)

    graph = GraphBuilder().build(project)
    CrossFileResolver().resolve(graph, str(project))
    store = GraphStore(project)
    store.save(graph)

    loaded = store.load()
    assert loaded is not None
    assert set(loaded.files.keys()) == set(graph.files.keys())
    assert set(loaded.functions.keys()) == set(graph.functions.keys())
    assert len(loaded.call_edges) == len(graph.call_edges)


# ---------------------------------------------------------------------------
# Full workflow via CLI
# ---------------------------------------------------------------------------

def test_e2e_cli_init_creates_graph(tmp_path):
    """CLI: init creates graph.json with expected structure."""
    project = tmp_path / "project"
    shutil.copytree(PYTHON_FIXTURE, project)
    runner = CliRunner()

    r = runner.invoke(cli, ["init", "--no-semantic", str(project)])
    assert r.exit_code == 0, r.output
    assert (project / ".winkers" / "graph.json").exists()

    graph_data = json.loads((project / ".winkers" / "graph.json").read_text())
    assert len(graph_data.get("functions", {})) >= 4
    assert len(graph_data.get("files", {})) >= 3


# ---------------------------------------------------------------------------
# TypeScript end-to-end
# ---------------------------------------------------------------------------

def test_e2e_typescript_full(tmp_path):
    """init + resolve on TypeScript fixture → locked function detected."""
    project = tmp_path / "ts"
    shutil.copytree(TS_FIXTURE, project)

    graph = GraphBuilder().build(project)
    CrossFileResolver().resolve(graph, str(project))

    assert len(graph.files) >= 3
    fn_names = [fn.name for fn in graph.functions.values()]
    assert "calculatePrice" in fn_names

    cp_id = next(fid for fid in graph.functions if "calculatePrice" in fid)
    assert graph.is_locked(cp_id)

    scope_result = _tool_scope(graph, {"function": cp_id})
    assert scope_result["function"]["locked"] is True
    assert "callers_constraint" in scope_result
