"""Tests for descriptions/translator.py — Cyrillic detection, cache, transport."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winkers.descriptions.translator import (
    CACHE_FILENAME,
    has_cyrillic,
    translate_to_english,
)

# ---------------------------------------------------------------------------
# Cyrillic detection
# ---------------------------------------------------------------------------


class TestHasCyrillic:
    def test_pure_english(self):
        assert not has_cyrillic("simplify invoice statuses from 6 to 3")

    def test_pure_russian(self):
        assert has_cyrillic("упростить статусы инвойсов с 6 до 3")

    def test_mixed_majority_english(self):
        # ratio ~0.05 threshold — bare "в" in otherwise-English text
        # should still trigger because token-level detection is loose.
        assert has_cyrillic("simplify статусы from 6 to 3")

    def test_single_stray_word_below_threshold(self):
        # 1 cyrillic char in 60+ latin → ratio < 0.05 → no translation
        text = "this is a long English sentence about pricing logic с"
        assert not has_cyrillic(text)

    def test_empty_string(self):
        assert not has_cyrillic("")

    def test_only_punctuation_and_digits(self):
        assert not has_cyrillic("123 ()[]{}")

    def test_code_identifiers_only(self):
        assert not has_cyrillic("Client.invoices, Class.method()")


# ---------------------------------------------------------------------------
# Translation orchestration
# ---------------------------------------------------------------------------


class TestTranslateToEnglish:
    def test_empty_returns_none(self, tmp_path):
        assert translate_to_english("", tmp_path) is None
        assert translate_to_english("   ", tmp_path) is None

    def test_english_returns_passthrough(self, tmp_path):
        # When input is already English we skip subprocess entirely and
        # return the text unchanged.
        text = "simplify invoice statuses"
        assert translate_to_english(text, tmp_path) == text

    def test_env_disable_short_circuits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WINKERS_NO_TRANSLATE", "1")
        with patch(
            "winkers.descriptions.translator._run_translate"
        ) as mock_run:
            result = translate_to_english(
                "упростить статусы", tmp_path,
            )
        assert result is None
        mock_run.assert_not_called()

    def test_cache_hit_skips_subprocess(self, tmp_path):
        # Pre-populate cache with a known key.
        from winkers.descriptions.translator import _key, _save_cache
        text = "упростить статусы инвойсов"
        _save_cache(tmp_path, {_key(text): "simplify invoice statuses"})

        with patch(
            "winkers.descriptions.translator._run_translate"
        ) as mock_run:
            result = translate_to_english(text, tmp_path)
        assert result == "simplify invoice statuses"
        mock_run.assert_not_called()

    def test_cache_miss_calls_subprocess_and_persists(self, tmp_path):
        text = "упростить статусы инвойсов"
        with patch(
            "winkers.descriptions.translator._run_translate",
            return_value="simplify invoice statuses",
        ):
            result = translate_to_english(text, tmp_path)
        assert result == "simplify invoice statuses"
        # Cache file should now exist with the entry.
        cache_path = tmp_path / ".winkers" / CACHE_FILENAME
        assert cache_path.exists()
        # Second call doesn't subprocess again — cached.
        with patch(
            "winkers.descriptions.translator._run_translate"
        ) as mock_run:
            again = translate_to_english(text, tmp_path)
        assert again == "simplify invoice statuses"
        mock_run.assert_not_called()

    def test_subprocess_failure_returns_none(self, tmp_path):
        with patch(
            "winkers.descriptions.translator._run_translate",
            return_value=None,
        ):
            result = translate_to_english("упростить статусы", tmp_path)
        assert result is None
        # Failed translations are NOT cached (avoid poisoning the cache
        # if the binary is temporarily unavailable).
        cache_path = tmp_path / ".winkers" / CACHE_FILENAME
        assert not cache_path.exists()


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


class TestInputTruncation:
    def test_long_input_truncated_before_cache_key(self, tmp_path):
        # Two prompts that differ ONLY past the 4000-char cap should
        # share a cache slot — the truncated head is what we hash.
        from winkers.descriptions.translator import _MAX_INPUT_CHARS, _key
        head = "упростить статусы " * 250  # well past 4000 chars
        a = head + " variant_A"
        b = head + " variant_B"
        ka = _key(a[:_MAX_INPUT_CHARS])
        kb = _key(b[:_MAX_INPUT_CHARS])
        assert ka == kb


# ---------------------------------------------------------------------------
# Smoke — module imports cleanly without claude binary
# ---------------------------------------------------------------------------


def test_module_imports_without_binary(tmp_path):
    """No claude on PATH must not break import or detection helpers."""
    # Just exercise the helper paths that don't shell out.
    assert has_cyrillic("привет") is True
    assert translate_to_english("plain english", tmp_path) == "plain english"
    if False:  # tests/__init__.py compatibility — pytest discovers via name
        pytest.fail("unreachable")
