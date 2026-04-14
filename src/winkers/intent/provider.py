"""Intent providers — generate one-sentence function descriptions via LLM."""

from __future__ import annotations

import logging
import os
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from winkers.models import FunctionNode
from winkers.store import STORE_DIR

log = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Analyze this function. Write ONE sentence: what it does, "
    "what it operates on, what approach it uses. "
    "Be specific about domain terms. Do not repeat the function name.\n\n"
    "```{language}\n{signature}\n{body_preview}\n```\n\nDescription:"
)

CONFIG_FILE = "config.toml"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class IntentConfig:
    provider: str = "auto"  # "auto" | "ollama" | "api" | "none"
    model: str = "gemma3:4b"
    ollama_url: str = "http://localhost:11434"
    api_model: str = "claude-haiku-4-5-20251001"
    prompt_template: str = DEFAULT_PROMPT
    temperature: float = 0.1
    max_tokens: int = 100


def load_config(root: Path) -> IntentConfig:
    """Load intent config from .winkers/config.toml [intent] section."""
    config_path = root / STORE_DIR / CONFIG_FILE
    if not config_path.exists():
        return IntentConfig()
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("intent", {})
        return IntentConfig(**{
            k: v for k, v in section.items()
            if k in IntentConfig.__dataclass_fields__
        })
    except Exception:
        return IntentConfig()


def save_config(root: Path, config: IntentConfig) -> None:
    """Save intent config to .winkers/config.toml."""
    config_path = root / STORE_DIR / CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                existing = tomllib.load(f)
        except Exception:
            pass

    existing["intent"] = {
        "provider": config.provider,
        "model": config.model,
        "ollama_url": config.ollama_url,
        "api_model": config.api_model,
        "prompt_template": config.prompt_template,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }

    # Write TOML manually (tomllib is read-only)
    lines: list[str] = []
    for section_name, section_data in existing.items():
        if isinstance(section_data, dict):
            lines.append(f"[{section_name}]")
            for k, v in section_data.items():
                lines.append(f"{k} = {_toml_value(v)}")
            lines.append("")

    config_path.write_text("\n".join(lines), encoding="utf-8")


def _toml_value(v) -> str:
    if isinstance(v, str):
        if "\n" in v:
            return f'"""\n{v}"""'
        return f'"{v}"'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{v}"'


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class IntentProvider(ABC):
    @abstractmethod
    def generate(self, fn: FunctionNode, source: str) -> str | None:
        """Generate a one-sentence intent for a function."""

    def generate_batch(
        self, functions: list[tuple[FunctionNode, str]],
    ) -> dict[str, str]:
        """Generate intents for multiple functions. Returns {fn_id: intent}."""
        results: dict[str, str] = {}
        for fn, source in functions:
            intent = self.generate(fn, source)
            if intent:
                results[fn.id] = intent
        return results


# ---------------------------------------------------------------------------
# NoneProvider
# ---------------------------------------------------------------------------

class NoneProvider(IntentProvider):
    def generate(self, fn: FunctionNode, source: str) -> str | None:
        return None


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

class OllamaProvider(IntentProvider):
    def __init__(self, config: IntentConfig) -> None:
        self.url = config.ollama_url.rstrip("/")
        self.model = config.model
        self.prompt_template = config.prompt_template
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    def generate(self, fn: FunctionNode, source: str) -> str | None:
        import httpx

        prompt = self._build_prompt(fn, source)
        try:
            resp = httpx.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": self.max_tokens,
                    },
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            return _clean_intent(text) if text else None
        except Exception as e:
            log.debug("Ollama intent failed for %s: %s", fn.id, e)
            return None

    def _build_prompt(self, fn: FunctionNode, source: str) -> str:
        sig = _fn_signature(fn)
        body = _body_preview(fn, source)
        return self.prompt_template.format(
            language=fn.language,
            signature=sig,
            body_preview=body,
        )


# ---------------------------------------------------------------------------
# ApiProvider (Anthropic / Haiku)
# ---------------------------------------------------------------------------

class ApiProvider(IntentProvider):
    def __init__(self, config: IntentConfig) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self.model = config.api_model
        self.prompt_template = config.prompt_template
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    def generate(self, fn: FunctionNode, source: str) -> str | None:
        sig = _fn_signature(fn)
        body = _body_preview(fn, source)
        prompt = self.prompt_template.format(
            language=fn.language,
            signature=sig,
            body_preview=body,
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            return _clean_intent(text) if text else None
        except Exception as e:
            log.debug("API intent failed for %s: %s", fn.id, e)
            return None


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def auto_detect(config: IntentConfig) -> IntentProvider:
    """Detect available provider: Ollama → API → None."""
    if config.provider == "none":
        return NoneProvider()
    if config.provider == "ollama":
        return OllamaProvider(config)
    if config.provider == "api":
        return ApiProvider(config)

    # auto: try Ollama first (server running + model pulled)
    if _ollama_available(config.ollama_url, config.model):
        log.info("Intent provider: Ollama (%s)", config.model)
        return OllamaProvider(config)

    # Fallback to API
    if os.environ.get("ANTHROPIC_API_KEY"):
        log.info("Intent provider: API (%s)", config.api_model)
        return ApiProvider(config)

    log.info("Intent provider: none (no Ollama or API key)")
    return NoneProvider()


def _ollama_available(url: str, model: str = "") -> bool:
    """Check if Ollama is running and has the requested model."""
    import httpx

    try:
        resp = httpx.get(
            f"{url.rstrip('/')}/api/tags",
            timeout=2.0,
        )
        if resp.status_code != 200:
            return False
        if not model:
            return True
        # Check if the model is actually pulled
        models = resp.json().get("models", [])
        model_base = model.split(":")[0]
        return any(
            m.get("name", "").startswith(model_base)
            for m in models
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fn_signature(fn: FunctionNode) -> str:
    """Build a readable signature string."""
    params = ", ".join(
        f"{p.name}: {p.type_hint}" if p.type_hint else p.name
        for p in fn.params
    )
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    prefix = "async " if fn.is_async else ""
    return f"{prefix}def {fn.name}({params}){ret}:"


def _body_preview(fn: FunctionNode, source: str, max_lines: int = 15) -> str:
    """Extract first N lines of the function body from source."""
    lines = source.splitlines()
    start = fn.line_start - 1  # 1-based → 0-based
    end = min(fn.line_end, len(lines))
    body_lines = lines[start:end]
    if len(body_lines) > max_lines:
        body_lines = body_lines[:max_lines] + ["    # ..."]
    return "\n".join(body_lines)


def _clean_intent(text: str) -> str:
    """Clean LLM output: strip quotes, limit to first sentence."""
    text = text.strip().strip('"').strip("'").strip()
    # Take first sentence only
    for sep in (".", "。", "\n"):
        if sep in text:
            text = text[:text.index(sep) + 1]
            break
    # Limit length
    if len(text) > 200:
        text = text[:197] + "..."
    return text
