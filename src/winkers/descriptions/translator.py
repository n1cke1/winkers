"""Pre-session query translator — Russian (or any non-English) → English.

Descriptions are authored in English (see `prompts.py`) so the BGE-M3
embedding space stays monolingual. Incoming user prompts in another
language need translation BEFORE the agent calls `orient(task=...)` /
`before_create(intent=...)` — otherwise we'd burn LLM time mid-session
on translation, or rely on weaker cross-lingual BGE-M3 retrieval.

The strategy is to translate inside the `prompt_enrich` UserPromptSubmit
hook (synchronously, ~3-5s) and inject the English form as additional
context. Result: zero in-session stall, agent receives the English
phrasing alongside the user's original prompt.

Transport: `claude --print` subprocess (subscription auth, $0 on Pro/Max
plans), same path as `descriptions/author.py`. Falls back to None when
the binary is missing or the call fails — the hook then degrades to
no translation rather than blocking the prompt.

Cache: project-level at `.winkers/translation_cache.json`, keyed by
sha256(text). Translations are idempotent and small, so the cache
persists across sessions without expiry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from winkers.store import STORE_DIR

log = logging.getLogger(__name__)

CACHE_FILENAME = "translation_cache.json"
_DEFAULT_TIMEOUT = 30  # seconds — cold model + short text usually < 10s
_MAX_INPUT_CHARS = 4000  # truncate very long prompts before translation


# ---------------------------------------------------------------------------
# Cyrillic detection — used to decide whether to translate at all
# ---------------------------------------------------------------------------

_CYRILLIC_LO = 0x0400
_CYRILLIC_HI = 0x04FF
_MIN_CYRILLIC_RATIO = 0.05  # > 5% Cyrillic letters → call it non-English


def has_cyrillic(text: str) -> bool:
    """True iff `text` contains a meaningful share of Cyrillic letters.

    Uses a 5%-ratio threshold so a stray transliterated word in an
    otherwise-English prompt doesn't trigger translation. The actual
    threshold is loose because the worst case is "we translated something
    that didn't need it" — which still produces a valid English string.
    """
    if not text:
        return False
    cyrillic = 0
    latin = 0
    for ch in text:
        cp = ord(ch)
        if _CYRILLIC_LO <= cp <= _CYRILLIC_HI:
            cyrillic += 1
        elif ch.isalpha() and cp < 0x80:
            latin += 1
    total = cyrillic + latin
    if total == 0:
        return False
    return (cyrillic / total) >= _MIN_CYRILLIC_RATIO


# ---------------------------------------------------------------------------
# Cache (project-level, persists across sessions)
# ---------------------------------------------------------------------------

def _cache_path(root: Path) -> Path:
    return root / STORE_DIR / CACHE_FILENAME


def _load_cache(root: Path) -> dict[str, str]:
    path = _cache_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(root: Path, cache: dict[str, str]) -> None:
    path = _cache_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def _key(text: str) -> str:
    """sha256 hex of the input — stable, collision-free across runs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate_to_english(
    text: str,
    root: Path,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str | None:
    """Translate `text` to English, caching the result under `root`.

    Returns:
      - the English translation on success
      - None on transport failure (claude binary missing, timeout,
        unparseable output) — caller should degrade gracefully

    Skips translation entirely when:
      - `WINKERS_NO_TRANSLATE=1` is set in env
      - `text` has no meaningful Cyrillic content (already English-ish)
      - `text` is empty
    """
    if not text or not text.strip():
        return None
    if os.environ.get("WINKERS_NO_TRANSLATE") == "1":
        return None
    if not has_cyrillic(text):
        # Already English-ish; the embedding step can use it verbatim.
        return text

    truncated = text[:_MAX_INPUT_CHARS]
    cache_key = _key(truncated)

    cache = _load_cache(root)
    cached = cache.get(cache_key)
    if cached:
        return cached

    translated = _run_translate(truncated, timeout=timeout)
    if translated is None:
        return None

    cache[cache_key] = translated
    _save_cache(root, cache)
    return translated


# ---------------------------------------------------------------------------
# Transport (claude --print subprocess)
# ---------------------------------------------------------------------------

_TRANSLATION_PROMPT = """\
Translate the following developer task description to English.

RULES:
- Output ONLY the English translation. No commentary, no explanation,
  no quote marks around the result.
- Keep code identifiers, file paths, function names, and bracket
  expressions VERBATIM (e.g. `Class.method()`, `app/repos/invoice.py`,
  `VALID_STATUSES`).
- Preserve the verb-first / imperative structure typical of dev tasks
  ("simplify X", "fix Y", "add Z").
- If the input is already English, output it unchanged.
- Do NOT add framing like "Translation:" or "Here is the translation:".

INPUT:
"""


def _resolve_claude_bin() -> str:
    import shutil
    if sys.platform == "win32":
        cmd = shutil.which("claude.cmd")
        if cmd:
            return cmd
    return shutil.which("claude") or "claude"


def _run_translate(text: str, *, timeout: int) -> str | None:
    """Spawn `claude --print` with the translation prompt + text.

    Same conventions as `descriptions/author.py::_run_claude`:
    tools-empty subscription path, runs in temp cwd to avoid triggering
    nested project hooks, drops CLAUDECODE env var.
    """
    import shutil
    import tempfile

    binary = _resolve_claude_bin()
    if not shutil.which(binary) and binary == "claude":
        log.debug("translator: claude binary not found, skipping translation")
        return None

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    full_prompt = _TRANSLATION_PROMPT + text

    cmd = [binary, "--print", "--allowedTools", ""]

    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            cwd=tempfile.gettempdir(),
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("translator: claude --print timed out after %ds", timeout)
        return None
    except FileNotFoundError:
        log.debug("translator: claude binary not on PATH")
        return None

    out = (result.stdout or "").strip()
    if not out:
        return None

    # Strip surrounding quotes the model occasionally adds despite the rule.
    if (out.startswith('"') and out.endswith('"')) or (
        out.startswith("'") and out.endswith("'")
    ):
        out = out[1:-1].strip()
    return out or None
