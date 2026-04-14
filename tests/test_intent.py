"""Tests for intent providers — auto-detection, generation, config."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from winkers.graph import GraphBuilder
from winkers.intent.provider import (
    IntentConfig,
    NoneProvider,
    OllamaProvider,
    _body_preview,
    _clean_intent,
    _fn_signature,
    auto_detect,
    load_config,
    save_config,
)
from winkers.resolver import CrossFileResolver

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_config(self):
        config = IntentConfig()
        assert config.provider == "auto"
        assert config.model == "gemma3:4b"
        assert config.temperature == 0.1

    def test_load_config_missing_file(self, tmp_path):
        config = load_config(tmp_path)
        assert config.provider == "auto"

    def test_save_and_load_config(self, tmp_path):
        (tmp_path / ".winkers").mkdir()
        config = IntentConfig(
            provider="ollama",
            model="gemma4:4b",
            temperature=0.2,
        )
        save_config(tmp_path, config)

        loaded = load_config(tmp_path)
        assert loaded.provider == "ollama"
        assert loaded.model == "gemma4:4b"
        assert loaded.temperature == 0.2

    def test_save_preserves_other_sections(self, tmp_path):
        (tmp_path / ".winkers").mkdir()
        config_path = tmp_path / ".winkers" / "config.toml"
        config_path.write_text(
            '[other]\nkey = "value"\n', encoding="utf-8"
        )
        save_config(tmp_path, IntentConfig())
        content = config_path.read_text(encoding="utf-8")
        assert "[other]" in content
        assert "[intent]" in content


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


class TestAutoDetect:
    def test_none_provider_explicit(self):
        config = IntentConfig(provider="none")
        provider = auto_detect(config)
        assert isinstance(provider, NoneProvider)

    def test_ollama_provider_explicit(self):
        config = IntentConfig(provider="ollama")
        provider = auto_detect(config)
        assert isinstance(provider, OllamaProvider)

    @patch("winkers.intent.provider._ollama_available", return_value=True)
    def test_auto_detects_ollama(self, mock_ollama):
        config = IntentConfig(provider="auto")
        provider = auto_detect(config)
        assert isinstance(provider, OllamaProvider)

    @patch("winkers.intent.provider._ollama_available", return_value=False)
    def test_auto_falls_back_to_none(self, mock_ollama):
        """No Ollama, no API key → NoneProvider."""
        config = IntentConfig(provider="auto")
        with patch.dict("os.environ", {}, clear=True):
            # Remove ANTHROPIC_API_KEY if present
            import os
            env = {k: v for k, v in os.environ.items()
                   if k != "ANTHROPIC_API_KEY"}
            with patch.dict("os.environ", env, clear=True):
                provider = auto_detect(config)
                assert isinstance(provider, NoneProvider)


# ---------------------------------------------------------------------------
# NoneProvider
# ---------------------------------------------------------------------------


class TestNoneProvider:
    def test_returns_none(self, graph):
        provider = NoneProvider()
        fn = list(graph.functions.values())[0]
        assert provider.generate(fn, "source") is None

    def test_batch_returns_empty(self, graph):
        provider = NoneProvider()
        fn = list(graph.functions.values())[0]
        assert provider.generate_batch([(fn, "source")]) == {}


# ---------------------------------------------------------------------------
# OllamaProvider (mocked)
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    def test_generate_success(self, graph):
        config = IntentConfig(model="test-model")
        provider = OllamaProvider(config)
        fn = list(graph.functions.values())[0]
        source = (PYTHON_FIXTURE / fn.file).read_text(encoding="utf-8")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "Calculates the total price with discounts applied."
        }

        with patch("httpx.post", return_value=mock_response):
            result = provider.generate(fn, source)

        assert result is not None
        assert "price" in result.lower() or len(result) > 5

    def test_generate_failure_returns_none(self, graph):
        config = IntentConfig()
        provider = OllamaProvider(config)
        fn = list(graph.functions.values())[0]

        with patch("httpx.post", side_effect=Exception("connection refused")):
            result = provider.generate(fn, "source")

        assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_fn_signature(self, graph):
        fn = graph.functions.get("modules/pricing.py::calculate_price")
        sig = _fn_signature(fn)
        assert "calculate_price" in sig
        assert "def " in sig

    def test_body_preview_truncates(self, graph):
        fn = list(graph.functions.values())[0]
        source = (PYTHON_FIXTURE / fn.file).read_text(encoding="utf-8")
        preview = _body_preview(fn, source, max_lines=3)
        lines = preview.splitlines()
        assert len(lines) <= 4  # 3 + possible "# ..."

    def test_clean_intent_strips_quotes(self):
        assert _clean_intent('"Hello world."') == "Hello world."

    def test_clean_intent_first_sentence(self):
        result = _clean_intent("First sentence. Second sentence.")
        assert result == "First sentence."

    def test_clean_intent_truncates_long(self):
        long = "x" * 300
        result = _clean_intent(long)
        assert len(result) <= 200


# ---------------------------------------------------------------------------
# Eval CLI
# ---------------------------------------------------------------------------


class TestEvalCli:
    def test_eval_intents_returns_results(self, graph):
        from winkers.intent.eval_cli import eval_intents

        provider = NoneProvider()
        results = eval_intents(
            graph, PYTHON_FIXTURE, provider, sample=3,
        )
        assert len(results) <= 3
        for r in results:
            assert "fn_id" in r
            assert "name" in r
            assert "generated_intent" in r

    def test_compare_intents_no_existing(self, graph):
        from winkers.intent.eval_cli import compare_intents

        provider = NoneProvider()
        results = compare_intents(graph, PYTHON_FIXTURE, provider)
        # No functions have intents in the fixture
        assert results == []


# ---------------------------------------------------------------------------
# Search boost with intent
# ---------------------------------------------------------------------------


class TestSearchWithIntent:
    def test_intent_boosts_search_score(self, graph):
        """Functions with matching intent get higher scores."""
        from winkers.search import invalidate_token_cache, search_functions

        # Set intent on a function with a cryptic name
        fn = list(graph.functions.values())[0]
        fn.intent = "calculates total price including discounts and tax"
        invalidate_token_cache([fn.id])

        results = search_functions(graph, "calculate price discount")
        # The function with intent should appear in results
        fn_ids = [m.fn.id for m in results]
        assert fn.id in fn_ids

        # Clean up
        fn.intent = None
        invalidate_token_cache([fn.id])
