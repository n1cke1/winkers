"""Tests for semantic layer."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from winkers.semantic import (
    EnrichResult,
    SemanticEnricher,
    SemanticLayer,
    SemanticStore,
    ZoneIntent,
    _graph_hash,
)


@pytest.fixture()
def sem_store(tmp_path):
    return SemanticStore(tmp_path)


@pytest.fixture()
def sample_layer():
    return SemanticLayer(
        data_flow="DB -> pricing -> API response.",
        domain_context="B2B invoicing with tax rules.",
        zone_intents={
            "modules": ZoneIntent(
                why="Core business logic.",
                wrong_approach="Importing DB here",
            )
        },
        new_feature_checklist=["1. Add service", "2. Add tests"],
    )


# --- SemanticStore ---

def test_store_save_load_roundtrip(sem_store, sample_layer):
    sem_store.save(sample_layer)
    loaded = sem_store.load()
    assert loaded is not None
    assert loaded.data_flow == "DB -> pricing -> API response."
    assert loaded.zone_intents["modules"].why == "Core business logic."
    assert len(loaded.new_feature_checklist) == 2


def test_store_load_nonexistent(sem_store):
    assert sem_store.load() is None


def test_store_save_creates_dir(tmp_path):
    store = SemanticStore(tmp_path / "sub" / "project")
    store.save(SemanticLayer())
    assert store.semantic_path.exists()


# --- Helpers ---

def test_file_zone_from_graph():
    """Graph.file_zone() returns stored zone or 'unknown' for missing paths."""
    from winkers.models import FileNode, Graph

    g = Graph()
    g.files["api/prices.py"] = FileNode(
        path="api/prices.py", language="python", imports=[], function_ids=[], zone="api",
    )
    g.files["models.py"] = FileNode(
        path="models.py", language="python", imports=[], function_ids=[], zone="models",
    )
    assert g.file_zone("api/prices.py") == "api"
    assert g.file_zone("models.py") == "models"
    assert g.file_zone("nonexistent.py") == "unknown"


def test_graph_hash_deterministic(tmp_path):
    from winkers.graph import GraphBuilder

    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    graph = GraphBuilder().build(tmp_path)
    h1 = _graph_hash(graph, tmp_path)
    h2 = _graph_hash(graph, tmp_path)
    assert h1 == h2
    assert len(h1) == 64


def test_graph_hash_changes(tmp_path):
    from winkers.graph import GraphBuilder

    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    graph = GraphBuilder().build(tmp_path)
    h1 = _graph_hash(graph, tmp_path)

    f.write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    h2 = _graph_hash(graph, tmp_path)
    assert h1 != h2


# --- SemanticEnricher ---

def _mock_anthropic():
    mock_mod = MagicMock()
    mock_client = MagicMock()
    mock_mod.Anthropic.return_value = mock_client
    return mock_mod, mock_client


def _make_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


SAMPLE_API_RESPONSE = json.dumps({
    "data_flow": "Input -> calc -> output.",
    "domain_context": "A pricing and inventory system.",
    "zone_intents": {
        "root": {
            "why": "Top-level utilities.",
            "wrong_approach": "Adding business logic here",
        }
    },
    "rules_audit": {
        "add": [
            {
                "category": "numeric",
                "title": "Positive prices",
                "content": "Prices must always be positive integers in cents.",
                "wrong_approach": "Using float — causes rounding errors",
                "affects": ["calc.py"],
                "related": ["data"],
            },
            {
                "category": "architecture",
                "title": "Pure functions in domain layer",
                "content": "Domain functions must be pure — no side effects.",
                "wrong_approach": "Side effects in pricing functions",
                "affects": [],
                "related": [],
            },
        ],
        "update": [],
        "remove": [],
    },
    "new_feature_checklist": ["1. Add function", "2. Add test"],
})


@patch.dict("sys.modules", {"anthropic": _mock_anthropic()[0]})
def test_enricher_enrich(tmp_path):
    from winkers.graph import GraphBuilder

    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    graph = GraphBuilder().build(tmp_path)

    mock_mod, mock_client = _mock_anthropic()
    mock_client.messages.create.return_value = _make_response(SAMPLE_API_RESPONSE)

    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        enricher = SemanticEnricher(api_key="test-key")
        enricher._client = mock_client
        result = enricher.enrich(graph, tmp_path)

    assert isinstance(result, EnrichResult)
    assert result.layer.data_flow == "Input -> calc -> output."
    assert "root" in result.layer.zone_intents
    assert len(result.rules_audit.add) == 2
    assert result.rules_audit.add[0].category == "numeric"
    assert result.rules_audit.add[0].affects == ["calc.py"]
    assert len(result.layer.new_feature_checklist) == 2
    assert "graph_hash" in result.layer.meta
    mock_client.messages.create.assert_called_once()


@patch.dict("sys.modules", {"anthropic": _mock_anthropic()[0]})
def test_enricher_proposed_rules_filtered(tmp_path):
    """Rules without title or content are dropped."""
    from winkers.graph import GraphBuilder

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a+b\n")
    graph = GraphBuilder().build(tmp_path)

    bad_response = json.dumps({
        "data_flow": "x",
        "domain_context": "y",
        "zone_intents": {},
        "rules_audit": {
            "add": [
                {"category": "data", "title": "", "content": "something"},   # no title
                {"category": "data", "title": "ok", "content": ""},           # no content
                {"category": "data", "title": "good", "content": "valid rule"},
            ],
        },
        "new_feature_checklist": [],
    })

    mock_mod, mock_client = _mock_anthropic()
    mock_client.messages.create.return_value = _make_response(bad_response)

    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        enricher = SemanticEnricher(api_key="test-key")
        enricher._client = mock_client
        result = enricher.enrich(graph, tmp_path)

    assert len(result.rules_audit.add) == 1
    assert result.rules_audit.add[0].title == "good"


@patch.dict("sys.modules", {"anthropic": _mock_anthropic()[0]})
def test_enricher_is_stale(tmp_path):
    from winkers.graph import GraphBuilder

    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    graph = GraphBuilder().build(tmp_path)

    mock_mod, _ = _mock_anthropic()
    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        enricher = SemanticEnricher(api_key="test-key")

    layer = SemanticLayer(meta={"graph_hash": _graph_hash(graph, tmp_path)})
    assert not enricher.is_stale(graph, tmp_path, layer)

    f.write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    assert enricher.is_stale(graph, tmp_path, layer)


@patch.dict("sys.modules", {"anthropic": _mock_anthropic()[0]})
def test_enricher_api_error(tmp_path):
    from winkers.graph import GraphBuilder

    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    graph = GraphBuilder().build(tmp_path)

    mock_mod, mock_client = _mock_anthropic()
    mock_client.messages.create.side_effect = Exception("API down")

    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        enricher = SemanticEnricher(api_key="test-key")
        enricher._client = mock_client
        with pytest.raises(RuntimeError, match="Semantic enrichment failed"):
            enricher.enrich(graph, tmp_path)


# --- CLI integration ---

def test_cli_init_no_semantic(tmp_path):
    """--no-semantic should skip API calls."""
    import shutil

    from click.testing import CliRunner

    from winkers.cli.main import cli

    fixtures = Path(__file__).parent / "fixtures" / "python_project"
    for f in fixtures.rglob("*"):
        if f.is_file():
            dst = tmp_path / f.relative_to(fixtures)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, dst)

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--no-semantic", str(tmp_path)])
    assert result.exit_code == 0
    assert "Semantic" not in result.output
    assert not (tmp_path / ".winkers" / "semantic.json").exists()
