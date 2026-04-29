"""winkers analyze."""

from __future__ import annotations

import os
from pathlib import Path

import click

from winkers.cli.init_pipeline import (
    _load_dotenv,
)


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--all", "analyze_all", is_flag=True, default=False,
              help="Analyze all recorded sessions, not just latest.")
def analyze(path: str, analyze_all: bool):
    """Analyze recorded sessions to find knowledge gaps.

    Sends session traces to Haiku (~$0.01/session) to identify what
    the agent didn't know.  Results accumulate in .winkers/insights.json
    with deduplication and priority escalation.

    \b
    By default analyzes only the most recent unanalyzed session.
    Use --all to analyze every recorded session.
    """
    _load_dotenv(Path(path).resolve())
    root = Path(path).resolve()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        click.echo("ANTHROPIC_API_KEY not set. Cannot analyze.")
        return

    from winkers.insights_store import InsightsStore
    from winkers.semantic import SemanticStore
    from winkers.session_store import SessionStore

    store = SessionStore(root)
    sessions = store.load_all()
    if not sessions:
        click.echo("No recorded sessions. Run: winkers record")
        return

    insights_store = InsightsStore(root)
    already_analyzed: set[str] = set()
    for item in insights_store.load():
        already_analyzed.update(item.session_ids)

    to_analyze = [
        s for s in sessions if s.session.session_id not in already_analyzed
    ]
    if not analyze_all and to_analyze:
        to_analyze = [to_analyze[-1]]

    if not to_analyze:
        click.echo("All sessions already analyzed.")
        return

    sem_store = SemanticStore(root)
    sem = sem_store.load()
    sem_json = sem.model_dump_json(indent=2) if sem else "{}"

    try:
        from winkers.analyzer import analyze_session
    except ImportError:
        click.echo("'anthropic' package required. Install with: pip install anthropic")
        return

    total_insights = 0
    for scored in to_analyze:
        click.echo(f"  Analyzing: {scored.session.task_prompt[:50]}...")
        try:
            result = analyze_session(scored, sem_json, api_key=api_key)
        except RuntimeError as e:
            click.echo(f"  Error: {e}")
            continue

        insights_store.merge(result)
        total_insights += len(result.insights)
        click.echo(
            f"    {len(result.insights)} insight(s) "
            f"({result.input_tokens + result.output_tokens} tokens)"
        )

    open_count = len(insights_store.open_insights())
    click.echo(
        f"\nDone. {total_insights} new insight(s), "
        f"{open_count} open total in insights.json."
    )
