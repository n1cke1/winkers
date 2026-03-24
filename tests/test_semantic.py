"""Tests for semantic layer (v2 — project-level intent/constraints/conventions)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from winkers.semantic import (
    Constraint,
    Convention,
    SemanticEnricher,
    SemanticLayer,
    SemanticStore,
    ZoneIntent,
    _graph_hash,
    _infer_zone,
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
        constraints=[
            Constraint(
                id="C001",
                name="Decimal money",
                why="float gives rounding errors",
                severity="critical",
                affects=["modules/pricing.py"],
            )
        ],
        conventions=[
            Convention(
                rule="Money as Decimal, never float.",
                wrong_approach="Using float for prices",
            )
        ],
        new_feature_checklist=["1. Add service", "2. Add tests"],
    )


# --- SemanticStore ---

def test_store_save_load_roundtrip(sem_store, sample_layer):
    sem_store.save(sample_layer)
    loaded = sem_store.load()
    assert loaded is not None
    assert loaded.data_flow == "DB -> pricing -> API response."
    assert loaded.zone_intents["modules"].why == "Core business logic."
    assert loaded.constraints[0].id == "C001"
    assert loaded.conventions[0].rule == "Money as Decimal, never float."
    assert len(loaded.new_feature_checklist) == 2


def test_store_load_nonexistent(sem_store):
    assert sem_store.load() is None


def test_store_save_creates_dir(tmp_path):
    store = SemanticStore(tmp_path / "sub" / "project")
    store.save(SemanticLayer())
    assert store.semantic_path.exists()


# --- Helpers ---

def test_infer_zone():
    assert _infer_zone("api/prices.py") == "api"
    assert _infer_zone("models.py") == "root"
    assert _infer_zone("src/main/App.java") == "src"


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
    "constraints": [
        {
            "id": "C001",
            "name": "Positive prices",
            "why": "Negative prices break invoicing",
            "severity": "critical",
            "affects": ["calc.py"],
        }
    ],
    "conventions": [
        {
            "rule": "Pure functions in domain layer.",
            "wrong_approach": "Side effects in pricing functions",
        }
    ],
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

    assert result.data_flow == "Input -> calc -> output."
    assert "root" in result.zone_intents
    assert len(result.constraints) == 1
    assert result.constraints[0].id == "C001"
    assert len(result.conventions) == 1
    assert len(result.new_feature_checklist) == 2
    assert "graph_hash" in result.meta
    mock_client.messages.create.assert_called_once()


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

    # Change code
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
