"""winkers record."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from winkers.store import GraphStore


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--transcript", type=click.Path(exists=True), default=None,
              help="Path to transcript.jsonl file.")
@click.option("--hook", is_flag=True, default=False,
              help="Read Claude Code hook JSON from stdin (SessionEnd).")
def record(path: str, transcript: str | None, hook: bool):
    """Record an agent session for learning.

    Parses Claude Code transcript, binds to git commit, computes
    tech debt delta, and scores the session. Results are saved to
    .winkers/sessions/.

    \b
    Modes:
      winkers record                  Find and record all unrecorded sessions
      winkers record --hook           Called by Claude Code SessionEnd hook (stdin)
      winkers record --transcript F   Record a specific transcript.jsonl file

    \b
    Automatic recording requires a Claude Code hook (not active by default):
      .claude/settings.json -> hooks -> SessionEnd ->
        { "type": "command", "command": "winkers record --hook" }
    """
    root = Path(path).resolve()

    if hook:
        _record_from_hook(root)
    elif transcript:
        from winkers.session_store import SessionStore
        _record_one(root, Path(transcript))
        _update_rule_stats(root, SessionStore(root))
    else:
        _record_catch_up(root)


def _record_from_hook(root: Path) -> None:
    """Read hook JSON from stdin, extract transcript_path, record it."""
    try:
        hook_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        click.echo("Error: could not parse hook JSON from stdin.", err=True)
        return

    transcript_path = hook_data.get("transcript_path", "")
    if not transcript_path:
        # Fallback: find transcript by session_id
        session_id = hook_data.get("session_id", "")
        if session_id:
            from winkers.recorder import find_project_transcripts
            for t in find_project_transcripts(root):
                if session_id in t.name:
                    transcript_path = str(t)
                    break

    if not transcript_path or not Path(transcript_path).exists():
        click.echo("Warning: transcript not found.", err=True)
        return

    _record_one(root, Path(transcript_path))
    from winkers.session_store import SessionStore
    _update_rule_stats(root, SessionStore(root))


def _record_one(root: Path, transcript_path: Path) -> None:
    """Parse one transcript and save scored session."""
    from winkers.recorder import parse_transcript
    from winkers.scoring import score_session
    from winkers.session_store import SessionStore

    session = parse_transcript(transcript_path)
    if not session.session_id:
        click.echo("Warning: could not parse session from transcript.", err=True)
        return

    # Check if already recorded
    store = SessionStore(root)
    if session.session_id in store.recorded_session_ids():
        click.echo(f"  Session {session.session_id[:8]} already recorded.")
        return

    # Load graph for debt delta (current graph only, before not available yet)
    graph = GraphStore(root).load()

    scored = score_session(session, root, graph_before=None, graph_after=graph)
    out_path = store.save(scored)

    from winkers.scoring import score_label
    label = score_label(scored.score)
    click.echo(
        f"  [ok] Recorded: {session.task_prompt[:50]}... "
        f"({session.total_turns} turns, score={scored.score:.2f} {label}) "
        f"-> {out_path.name}"
    )

    # Redo detection: warn if same task was previously rejected
    _check_redo(root, store, scored)


REDO_WARNING_FILE = ".winkers/redo_warning.md"


def _update_rule_stats(root: Path, store) -> None:
    """Recompute rule stats from all recorded sessions and save to rules.json."""
    from winkers.conventions import RulesStore, RuleStats

    rules_store = RulesStore(root)
    if not rules_store.exists():
        return
    rules_file = rules_store.load()
    if not rules_file.rules:
        return

    by_category = {r.category: r for r in rules_file.rules}
    for rule in rules_file.rules:
        rule.stats = RuleStats()

    for scored in store.load_all():
        for tc in scored.session.tool_calls:
            if tc.name == "mcp__winkers__rule_read":
                category = tc.input_params.get("category", "")
                if category in by_category:
                    by_category[category].stats.times_requested += 1

    rules_store.save(rules_file)


def _check_redo(root: Path, store, scored) -> None:
    """Create or clear redo warning based on task history."""
    redo_path = root / REDO_WARNING_FILE
    task_hash = scored.session.task_hash
    previous = store.find_by_task_hash(task_hash)

    # Clear warning if this attempt succeeded
    if scored.score > 0.7 and redo_path.exists():
        redo_path.unlink()
        click.echo("  [ok] Redo warning cleared (session succeeded).")
        return

    # Check if a previous attempt on same task was rejected
    rejected = [
        s for s in previous
        if s.session.session_id != scored.session.session_id
        and s.score < 0.4
    ]
    if not rejected:
        return

    last_rejected = rejected[-1]
    warning = (
        f"Previous attempt at task \"{scored.session.task_prompt[:60]}\" "
        f"had low score ({last_rejected.score:.2f}).\n"
    )
    if last_rejected.debt.complexity_delta > 0:
        warning += (
            f"Reason: complexity grew by {last_rejected.debt.complexity_delta}.\n"
        )
    if last_rejected.session.user_corrections:
        warning += (
            f"User feedback: {last_rejected.session.user_corrections[0]}\n"
        )
    warning += "Consider a different approach.\n"

    redo_path.parent.mkdir(parents=True, exist_ok=True)
    redo_path.write_text(warning, encoding="utf-8")
    click.echo(f"  [!] Redo warning created: {REDO_WARNING_FILE}")


def _record_catch_up(root: Path) -> None:
    """Find all unrecorded transcripts for this project."""
    from winkers.recorder import find_project_transcripts
    from winkers.session_store import SessionStore

    store = SessionStore(root)
    recorded = store.recorded_session_ids()
    transcripts = find_project_transcripts(root)

    if not transcripts:
        click.echo("No transcripts found for this project.")
        return

    new_count = 0
    for t in transcripts:
        # Quick check: extract session_id from first line
        try:
            first_line = t.open(encoding="utf-8").readline()
            data = json.loads(first_line)
            sid = data.get("sessionId", "")
            if sid and sid in recorded:
                continue
        except Exception:
            continue

        _record_one(root, t)
        new_count += 1

    if new_count == 0:
        click.echo("All sessions already recorded.")
    else:
        click.echo(f"Recorded {new_count} new session(s).")
        _update_rule_stats(root, store)
