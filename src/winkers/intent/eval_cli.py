"""Intent eval CLI — test and compare intent prompts."""

from __future__ import annotations

import random
from pathlib import Path

from winkers.intent.provider import IntentProvider, _fn_signature
from winkers.models import FunctionNode, Graph


def eval_intents(
    graph: Graph,
    root: Path,
    provider: IntentProvider,
    sample: int = 20,
    prompt_override: str | None = None,
) -> list[dict]:
    """Generate intents for a sample of functions. Returns list of results."""
    functions = list(graph.functions.values())
    if len(functions) > sample:
        functions = random.sample(functions, sample)

    results: list[dict] = []
    for fn in functions:
        source = _read_source(root, fn)
        if source is None:
            continue

        if prompt_override and hasattr(provider, "prompt_template"):
            old_template = provider.prompt_template
            provider.prompt_template = prompt_override

        intent = provider.generate(fn, source)

        if prompt_override and hasattr(provider, "prompt_template"):
            provider.prompt_template = old_template

        results.append({
            "fn_id": fn.id,
            "name": fn.name,
            "file": fn.file,
            "signature": _fn_signature(fn),
            "existing_intent": fn.intent,
            "generated_intent": intent,
            "lines": fn.lines,
        })

    return results


def compare_intents(
    graph: Graph,
    root: Path,
    provider: IntentProvider,
    sample: int = 20,
) -> list[dict]:
    """Compare existing intents with freshly generated ones."""
    # Only include functions that already have intents
    with_intent = [
        fn for fn in graph.functions.values() if fn.intent
    ]
    if not with_intent:
        return []

    if len(with_intent) > sample:
        with_intent = random.sample(with_intent, sample)

    results: list[dict] = []
    for fn in with_intent:
        source = _read_source(root, fn)
        if source is None:
            continue

        new_intent = provider.generate(fn, source)
        results.append({
            "fn_id": fn.id,
            "name": fn.name,
            "current": fn.intent,
            "new": new_intent,
            "changed": fn.intent != new_intent,
        })

    return results


def _read_source(root: Path, fn: FunctionNode) -> str | None:
    """Read source file for a function."""
    file_path = root / fn.file
    if not file_path.exists():
        return None
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception:
        return None
