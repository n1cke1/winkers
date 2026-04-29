"""`winkers intent ...` — LLM intent generation tooling."""

from __future__ import annotations

import json
from pathlib import Path

import click

from winkers.store import GraphStore


@click.group()
def intent():
    """LLM intent generation and evaluation."""


@intent.command("eval")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--sample", "-n", default=20, help="Number of functions to sample.")
@click.option("--prompt", "prompt_override", default=None, type=str,
              help="Test an alternative prompt template.")
@click.option("--compare", is_flag=True, default=False,
              help="Compare existing intents with freshly generated ones.")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Output as JSON.")
def intent_eval(path: str, sample: int, prompt_override: str | None,
                compare: bool, as_json: bool):
    """Evaluate intent generation quality.

    Requires a configured provider: ANTHROPIC_API_KEY (default)
    or --ollama in .winkers/config.toml.

    \b
    Examples:
        winkers intent eval --sample 10 --json
        winkers intent eval --prompt "Describe this function:" --json
        winkers intent eval --compare
    """
    from winkers.intent.eval_cli import compare_intents, eval_intents
    from winkers.intent.provider import auto_detect, load_config

    root = Path(path).resolve()
    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    config = load_config(root)
    provider = auto_detect(config)

    from winkers.intent.provider import NoneProvider
    if isinstance(provider, NoneProvider):
        click.echo("Error: no LLM provider available.", err=True)
        raise SystemExit(1)

    if compare:
        results = compare_intents(graph, root, provider, sample=sample)
        if as_json:
            click.echo(json.dumps(results, indent=2))
        else:
            for r in results:
                changed = "CHANGED" if r["changed"] else "same"
                click.echo(f"  [{changed}] {r['name']}")
                click.echo(f"    current: {r['current']}")
                click.echo(f"    new:     {r['new']}")
        return

    results = eval_intents(
        graph, root, provider,
        sample=sample, prompt_override=prompt_override,
    )

    if as_json:
        click.echo(json.dumps(results, indent=2))
    else:
        for r in results:
            click.echo(f"  {r['name']} ({r['file']})")
            click.echo(f"    sig:    {r['signature']}")
            click.echo(f"    intent: {r['generated_intent']}")
            click.echo()
        click.echo(f"  {len(results)} functions evaluated.")
