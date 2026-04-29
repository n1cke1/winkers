"""Tests for project-level language detection + config lock."""

from __future__ import annotations

from pathlib import Path

from winkers.project_config import (
    DEFAULT_LANGUAGE,
    detect_project_language,
    get_project_language,
    save_project_language,
)

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_empty_repo_returns_default(self, tmp_path: Path) -> None:
        assert detect_project_language(tmp_path) == DEFAULT_LANGUAGE

    def test_english_repo(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "# Calculate price for invoice items.\n"
            "# Assumes USD currency unless overridden.\n"
            "def calculate_price(items): return sum(i.price for i in items)\n",
            encoding="utf-8",
        )
        assert detect_project_language(tmp_path) == "en"

    def test_russian_repo(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "# Расчёт стоимости товаров в накладной.\n"
            "# По умолчанию рубли, если не указано иное.\n"
            "def рассчитать_цену(товары): return sum(t.цена for t in товары)\n",
            encoding="utf-8",
        )
        assert detect_project_language(tmp_path) == "ru"

    def test_mixed_dominantly_english(self, tmp_path: Path) -> None:
        # Single Russian comment in an otherwise English file —
        # ratio threshold (20%) keeps the verdict "en".
        (tmp_path / "main.py").write_text(
            "# Calculate the price for invoice items.\n"
            "# Returns total in float currency units.\n"
            "# Считает стоимость.\n"
            "def calc(items): return sum(i.price for i in items)\n"
            "def total(invoices): return sum(i.total for i in invoices)\n",
            encoding="utf-8",
        )
        assert detect_project_language(tmp_path) == "en"

    def test_excludes_vendored_dirs(self, tmp_path: Path) -> None:
        # A stuffed node_modules with Russian must NOT pull the verdict over.
        (tmp_path / "main.py").write_text(
            "# Calculate the price for invoice items.\n",
            encoding="utf-8",
        )
        nm = tmp_path / "node_modules" / "junk"
        nm.mkdir(parents=True)
        for i in range(20):
            (nm / f"f{i}.js").write_text(
                "// Расчёт стоимости товаров в накладной\n" * 50,
                encoding="utf-8",
            )
        assert detect_project_language(tmp_path) == "en"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_get(self, tmp_path: Path) -> None:
        save_project_language(tmp_path, "ru")
        assert get_project_language(tmp_path) == "ru"

    def test_get_missing_returns_default(self, tmp_path: Path) -> None:
        assert get_project_language(tmp_path) == DEFAULT_LANGUAGE

    def test_save_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        save_project_language(tmp_path, "ru")
        # User explicitly set "ru" — a later auto-detect of "en" must not
        # silently rewrite it.
        save_project_language(tmp_path, "en")
        assert get_project_language(tmp_path) == "ru"

    def test_save_unsupported_lang_is_noop(self, tmp_path: Path) -> None:
        save_project_language(tmp_path, "klingon")
        assert get_project_language(tmp_path) == DEFAULT_LANGUAGE

    def test_save_preserves_other_sections(self, tmp_path: Path) -> None:
        from winkers.store import STORE_DIR
        cfg = tmp_path / STORE_DIR / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "[intent]\nprovider = \"api\"\nmodel = \"haiku\"\n",
            encoding="utf-8",
        )
        save_project_language(tmp_path, "ru")
        text = cfg.read_text(encoding="utf-8")
        assert "[intent]" in text
        assert 'provider = "api"' in text
        assert 'language = "ru"' in text
