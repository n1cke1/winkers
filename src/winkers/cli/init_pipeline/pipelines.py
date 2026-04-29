"""Smaller init sub-pipelines — impact, intent, debt, git history."""

from __future__ import annotations

import json
from pathlib import Path

import click

from winkers.cli.init_pipeline.bootstrap import _load_dotenv
from winkers.store import GraphStore


def _run_impact_only(root: Path, force: bool) -> None:
    """--impact-only: load existing graph, run LLM impact pass, save."""
    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        click.echo(
            "  No graph.json found. Run `winkers init` first, or drop "
            "--impact-only for a full pass."
        )
        return
    ok = _run_impact_generation(root, graph, force=force)
    if ok:
        store.save(graph)


def _run_impact_generation(root: Path, graph, force: bool) -> bool:
    """Run the combined intent+impact pass. Returns True if it actually ran.

    Works with both Claude API (preferred) and Ollama (via format=json).
    Returns False only when no LLM provider is configured at all, in which
    case the caller falls back to the legacy single-intent generator.
    """
    _load_dotenv(root)

    from winkers.impact import ImpactGenerator, ImpactStore
    from winkers.intent.provider import (
        ApiProvider,
        NoneProvider,
        OllamaProvider,
        auto_detect,
        load_config,
    )

    intent_cfg = load_config(root)
    provider = auto_detect(intent_cfg)
    if isinstance(provider, NoneProvider):
        return False
    if not isinstance(provider, (ApiProvider, OllamaProvider)):
        return False

    label = "Claude API" if isinstance(provider, ApiProvider) else f"Ollama ({provider.model})"
    click.echo(f"  Running impact analysis ({label}) ...")
    impact_store = ImpactStore(root)
    impact_file = impact_store.load()

    gen = ImpactGenerator(graph, root, force=force)
    impact_file = gen.run(impact_file=impact_file, progress_factory=click.progressbar)
    impact_store.save(impact_file)

    meta = impact_file.meta
    click.echo(
        f"  [ok] Impact: {meta.functions_analyzed} analyzed, "
        f"{meta.functions_skipped} cached, "
        f"{meta.functions_failed} failed "
        f"({meta.duration_seconds}s, {meta.llm_model})"
    )
    return True


def _collect_git_history(root: Path, graph) -> None:
    """Collect recent git commits per file and store in graph."""
    from winkers.git import run_git

    stdout = run_git(
        ["log", "-20", "--pretty=format:%H|%an|%ad|%s",
         "--date=short", "--name-only"],
        cwd=root, timeout=15,
    )
    if not stdout:
        return

    # Parse: commit lines alternate with file lists
    commits_by_file: dict[str, list[dict]] = {}
    current_commit: dict | None = None

    for line in stdout.splitlines():
        if "|" in line and len(line.split("|", 3)) == 4:
            sha, author, date, message = line.split("|", 3)
            current_commit = {
                "sha": sha[:8], "author": author,
                "date": date, "message": message,
            }
        elif line.strip() and current_commit:
            path = line.strip().replace("\\", "/")
            if path not in commits_by_file:
                commits_by_file[path] = []
            if len(commits_by_file[path]) < 5:
                commits_by_file[path].append(current_commit)

    count = 0
    for path, file_node in graph.files.items():
        norm = path.replace("\\", "/")
        if norm in commits_by_file:
            file_node.recent_commits = commits_by_file[norm]
            count += 1

    if count:
        click.echo(f"  [ok] Git history: {count} files with commits")


def _run_debt_analysis(root: Path, graph) -> None:
    """Compute tech debt metrics and save to .winkers/debt.json."""
    from winkers.debt import compute_debt

    report = compute_debt(graph)
    debt_path = root / ".winkers" / "debt.json"
    debt_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    s = report.summary
    total = s.get("total_issues", 0)
    high = s.get("by_severity", {}).get("high", 0)
    medium = s.get("by_severity", {}).get("medium", 0)
    score = s.get("score", 0)
    density = s.get("density", 0.0)

    if total == 0:
        click.echo("  [ok] Tech debt: clean")
    else:
        click.echo(
            f"  Tech debt: {total} issues "
            f"({high} high, {medium} medium) "
            f"score={score} density={density}/100fn -> .winkers/debt.json"
        )


def _run_intent_generation(
    root: Path, graph, ollama_model: str | None = None,
) -> None:
    """Generate per-function LLM intents (Ollama / API / none)."""
    from winkers.intent.provider import (
        auto_detect,
        load_config,
        save_config,
    )

    config = load_config(root)

    # CLI overrides
    if ollama_model:
        config.provider = "ollama"
        config.model = ollama_model
        save_config(root, config)

    provider = auto_detect(config)

    # Skip if NoneProvider
    from winkers.intent.provider import NoneProvider
    if isinstance(provider, NoneProvider):
        click.echo(
            "  ~ Intent generation skipped (no API key)."
            " Use --ollama MODEL for local generation."
        )
        return

    # Find functions without intent
    needs_intent = [
        fn for fn in graph.functions.values() if not fn.intent
    ]
    if not needs_intent:
        click.echo(
            f"  [ok] All {len(graph.functions)} functions already have intents."
        )
        return

    # Warmup: verify provider works with a quick test
    if not _intent_provider_ready(provider, needs_intent[0], root):
        click.echo("  ~ Intent generation skipped (provider not ready).")
        return

    click.echo(
        f"Generating intents for {len(needs_intent)} functions "
        f"({type(provider).__name__}) ..."
    )

    generated = 0
    for i, fn in enumerate(needs_intent):
        source = _read_fn_source(root, fn)
        if source is None:
            continue
        intent = provider.generate(fn, source)
        if intent:
            fn.intent = intent
            generated += 1
        if (i + 1) % 20 == 0:
            click.echo(f"  ... {i + 1}/{len(needs_intent)}")

    if generated:
        store = GraphStore(root)
        store.save(graph)

    click.echo(f"  [ok] Generated {generated}/{len(needs_intent)} intents.")


def _intent_provider_ready(provider, fn, root: Path) -> bool:
    """Quick check: can the provider generate an intent?"""
    source = _read_fn_source(root, fn)
    if source is None:
        return False
    try:
        # Use a short timeout for the warmup test
        from winkers.intent.provider import OllamaProvider
        if isinstance(provider, OllamaProvider):
            import httpx
            prompt = provider._build_prompt(fn, source)
            resp = httpx.post(
                f"{provider.url}/api/generate",
                json={
                    "model": provider.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": provider.temperature,
                        "num_predict": 20,
                    },
                },
                timeout=10.0,
            )
            return resp.status_code == 200
        # For API provider, assume it works
        return True
    except Exception:
        return False


def _read_fn_source(root: Path, fn) -> str | None:
    """Read source file for a function."""
    file_path = root / fn.file
    if not file_path.exists():
        return None
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception:
        return None
