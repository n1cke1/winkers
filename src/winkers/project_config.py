"""Project-level config: language lock + future settings.

`detect_project_language(root)` samples a handful of source files and
returns the dominant natural language used in comments/docstrings.
The result is locked into `.winkers/config.toml::[project].language`
at `winkers init` time so LLM-authored unit descriptions stay in one
language regardless of which file fragment the LLM was shown.

This addresses Issue 2 (description language drifts to source-text
language) from ISSUES_run_I9_observations.md — option B.
"""

from __future__ import annotations

import logging
import random
import tomllib
from pathlib import Path

from winkers.store import STORE_DIR

log = logging.getLogger(__name__)

CONFIG_FILE = "config.toml"
SECTION = "project"
KEY_LANGUAGE = "language"

# Languages we know how to author in. The detector returns one of these
# (or "en" as fallback when signal is weak).
SUPPORTED_LANGUAGES = ("en", "ru")
DEFAULT_LANGUAGE = "en"

# Cyrillic Unicode range. We don't try to distinguish between Slavic
# languages — RU/UK/BG/SR all read close enough at the description
# level for retrieval, and BGE-M3 handles them uniformly.
_CYRILLIC_LO = 0x0400
_CYRILLIC_HI = 0x04FF

# Sampling parameters chosen for fast init: ≤16 files, ≤8 KB each →
# at most ~128 KB of text, hashed in milliseconds.
_SAMPLE_FILES = 16
_SAMPLE_BYTES = 8 * 1024
_MIN_CYRILLIC_RATIO = 0.20  # > 20% Cyrillic letters → call it "ru"

_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".cs")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_project_language(root: Path) -> str:
    """Return the dominant natural language of comments/docstrings.

    Strategy: sample up to `_SAMPLE_FILES` source files, read the first
    `_SAMPLE_BYTES` of each, count Cyrillic vs Latin letters across the
    pooled text. If Cyrillic letters exceed `_MIN_CYRILLIC_RATIO` of
    total letters, return "ru"; else "en".

    Returns DEFAULT_LANGUAGE when there are no source files at all.
    """
    candidates: list[Path] = []
    for ext in _CODE_EXTS:
        candidates.extend(root.rglob(f"*{ext}"))
        if len(candidates) >= _SAMPLE_FILES * 4:
            break

    candidates = [
        p for p in candidates
        if not _is_excluded_path(p, root)
    ]

    if not candidates:
        return DEFAULT_LANGUAGE

    rng = random.Random(0)
    sample = rng.sample(candidates, k=min(_SAMPLE_FILES, len(candidates)))

    cyrillic = 0
    latin = 0
    for path in sample:
        try:
            with path.open("rb") as f:
                blob = f.read(_SAMPLE_BYTES)
            text = blob.decode("utf-8", errors="replace")
        except OSError:
            continue
        for ch in text:
            cp = ord(ch)
            if _CYRILLIC_LO <= cp <= _CYRILLIC_HI:
                cyrillic += 1
            elif ch.isalpha() and cp < 0x80:
                latin += 1

    total = cyrillic + latin
    if total == 0:
        return DEFAULT_LANGUAGE

    ratio = cyrillic / total
    return "ru" if ratio >= _MIN_CYRILLIC_RATIO else "en"


def _is_excluded_path(path: Path, root: Path) -> bool:
    """Skip vendored / build / cache trees that pollute the language signal."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = {p.lower() for p in rel.parts}
    excluded = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        "site-packages", "vendor", "third_party",
    }
    return bool(parts & excluded)


# ---------------------------------------------------------------------------
# Persistence in config.toml
# ---------------------------------------------------------------------------

def get_project_language(root: Path) -> str:
    """Read locked language from config.toml; falls back to detection.

    Callers that want a single read-only value should use this — it
    avoids re-detecting on every prompt build.
    """
    config_path = root / STORE_DIR / CONFIG_FILE
    if config_path.exists():
        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
            lang = data.get(SECTION, {}).get(KEY_LANGUAGE)
            if lang in SUPPORTED_LANGUAGES:
                return lang
        except Exception as e:
            log.debug("project_config: cannot read %s (%s)", config_path, e)
    return DEFAULT_LANGUAGE


def save_project_language(root: Path, language: str) -> None:
    """Persist `[project].language = "<lang>"` in config.toml.

    Merges with any other sections that already exist (intent, impact,
    data_files). Does NOT overwrite an existing `language` value — the
    user may have set it manually.
    """
    if language not in SUPPORTED_LANGUAGES:
        log.warning(
            "project_config: refusing to save unsupported language %r", language
        )
        return

    config_path = root / STORE_DIR / CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as f:
                existing = tomllib.load(f)
        except Exception:
            existing = {}

    project_section = existing.get(SECTION, {})
    if isinstance(project_section, dict) and project_section.get(KEY_LANGUAGE):
        # Already set — respect user's choice.
        return

    if not isinstance(project_section, dict):
        project_section = {}
    project_section[KEY_LANGUAGE] = language
    existing[SECTION] = project_section

    config_path.write_text(_dump_toml(existing), encoding="utf-8")


def _dump_toml(data: dict) -> str:
    """Minimal TOML writer for our flat-section config.

    tomllib is read-only in stdlib; we can't depend on `tomli_w`. Our
    config is shallow (top-level sections with primitive values), so a
    hand-rolled dumper is enough.
    """
    out: list[str] = []
    for section_name, section in data.items():
        if not isinstance(section, dict):
            continue
        out.append(f"[{section_name}]")
        for k, v in section.items():
            out.append(f"{k} = {_toml_value(v)}")
        out.append("")
    return "\n".join(out)


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        if "\n" in v:
            return f'"""{escaped}"""'
        return f'"{escaped}"'
    return f'"{v}"'
