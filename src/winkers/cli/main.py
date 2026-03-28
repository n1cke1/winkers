"""Winkers CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from winkers.conventions import RulesAudit, RulesFile, RulesStore

from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore


@click.group()
@click.version_option(version=__import__("winkers").__version__)
@click.pass_context
def cli(ctx: click.Context):
    """Winkers -- architectural context layer for AI coding agents.

    \b
    Quick start:
      1. Set API key:  set ANTHROPIC_API_KEY=sk-ant-...
         (or create .env file in project root)
      2. winkers init          Build graph + semantic
      3. winkers serve         Start MCP server for AI agents
      4. winkers dashboard     Open browser graph

    \b
    Session recording (learn from agent sessions):
      winkers record             Record unrecorded sessions (catch-up)
      winkers record --catch-up  Same -- scan all transcripts, record new ones

    \b
    Recording is NOT active by default. To enable automatic recording
    after every Claude Code session, add a SessionEnd hook:

    \b
      .claude/settings.json -> hooks -> SessionEnd ->
        { "type": "command", "command": "winkers record --hook" }

    \b
    Without the hook, run  winkers record  manually to catch up.
    """


@cli.result_callback()
def _after_command(*_args, **_kwargs):
    """Print update notice if a newer version is available on PyPI."""
    import winkers
    from winkers.version_check import newer_version_available

    latest = newer_version_available(winkers.__version__)
    if latest:
        click.echo(
            f"\n  Update available: {winkers.__version__} → {latest}\n"
            f"  Run: pip install --upgrade winkers",
            err=True,
        )


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--no-semantic", is_flag=True, default=False,
              help="Skip semantic enrichment (no Claude API call).")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Accept all proposed rule changes without interactive review.")
def init(path: str, no_semantic: bool, yes: bool):
    """Build the dependency graph for the project.

    Automatically detects your IDE and registers the MCP server:

    \b
      .claude/ or CLAUDE.md found  ->  Claude Code config
      .cursor/ found               ->  Cursor rules

    Semantic enrichment requires ANTHROPIC_API_KEY. Set it via:

    \b
      export ANTHROPIC_API_KEY=sk-ant-...   (Linux/Mac)
      set ANTHROPIC_API_KEY=sk-ant-...      (Windows cmd)
      $env:ANTHROPIC_API_KEY="sk-ant-..."   (PowerShell)

    Or create a .env file in the project root with ANTHROPIC_API_KEY=sk-ant-...

    If the key is not set, init still works -- semantic is skipped.
    Use --no-semantic to skip explicitly.

    \b
    Corporate SSL proxy? Two options:
      1. pip install pip-system-certs   (recommended, one-time fix)
      2. set WINKERS_SSL_VERIFY=0       (quick workaround, less secure)
    """
    root = Path(path).resolve()
    click.echo(f"Scanning {root} ...")

    builder = GraphBuilder()
    graph = builder.build(root)

    click.echo("Resolving cross-file calls ...")
    CrossFileResolver().resolve(graph, str(root))

    _collect_git_history(root, graph)

    store = GraphStore(root)
    store.save(graph)
    _save_history_snapshot(root, graph)

    _update_gitignore(root)

    click.echo(
        f"Done. {len(graph.files)} files, {len(graph.functions)} functions, "
        f"{len(graph.call_edges)} call edges. ({graph.meta.get('parse_time_ms', 0):.0f} ms)"
    )

    _run_debt_analysis(root, graph)

    if not no_semantic:
        _run_semantic_enrichment(root, graph, yes=yes)

    _autodetect_ide(root)


def _collect_git_history(root: Path, graph) -> None:
    """Collect recent git commits per file and store in graph."""
    import subprocess
    import sys

    try:
        kwargs: dict = {
            "capture_output": True, "text": True,
            "cwd": str(root), "timeout": 15,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            ["git", "log", "-20", "--pretty=format:%H|%an|%ad|%s",
             "--date=short", "--name-only"],
            **kwargs,
        )
    except Exception:
        return

    if result.returncode != 0:
        return

    # Parse: commit lines alternate with file lists
    commits_by_file: dict[str, list[dict]] = {}
    current_commit: dict | None = None

    for line in result.stdout.splitlines():
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


MAX_SNAPSHOTS = 20


def _save_history_snapshot(root: Path, graph) -> None:
    """Save a timestamped copy of graph.json to .winkers/history/."""
    from datetime import datetime

    history_dir = root / ".winkers" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_path = history_dir / f"{ts}.json"
    snapshot_path.write_text(
        graph.model_dump_json(indent=2, exclude_defaults=True),
        encoding="utf-8",
    )

    # Cleanup: keep only latest MAX_SNAPSHOTS
    snapshots = sorted(history_dir.glob("*.json"))
    if len(snapshots) > MAX_SNAPSHOTS:
        for old in snapshots[:-MAX_SNAPSHOTS]:
            old.unlink()
        removed = len(snapshots) - MAX_SNAPSHOTS
        click.echo(f"  [ok] History snapshot: {snapshot_path.name} ({removed} old removed)")
    else:
        click.echo(f"  [ok] History snapshot: {snapshot_path.name}")


def _load_dotenv(root: Path) -> None:
    """Load .env file from project root into os.environ."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _run_semantic_enrichment(root: Path, graph, yes: bool = False) -> None:
    """One Claude API call -- generate architectural context and audit rules."""
    _load_dotenv(root)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        click.echo(
            "  Skipping semantic: ANTHROPIC_API_KEY not set.\n"
            "  Set it via: set ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Or create a .env file in the project root."
        )
        return
    click.echo(f"  API key found: {api_key[:12]}...")

    try:
        from winkers.semantic import (
            SemanticEnricher,
            SemanticStore,
            build_insights_prompt,
        )
    except ImportError:
        click.echo(
            "  Skipping semantic: 'anthropic' not installed. "
            "Run: pip install anthropic"
        )
        return

    from winkers.conventions import (
        DismissedStore,
        RulesStore,
        compile_overview,
    )
    from winkers.detectors import run_all_detectors

    rules_store = RulesStore(root)
    dismissed_store = DismissedStore(root)
    rules_file = rules_store.load()
    dismissed = dismissed_store.load()

    sem_store = SemanticStore(root)
    existing = sem_store.load()

    try:
        enricher = SemanticEnricher()
    except Exception as e:
        click.echo(f"  Skipping semantic: {e}")
        return

    insights_text = build_insights_prompt(root)
    has_new_insights = bool(insights_text)

    if existing and not enricher.is_stale(graph, root, existing) and not has_new_insights:
        click.echo("  Semantic data up to date, skipping API call.")
        return

    if has_new_insights:
        click.echo("  Including insights from past agent sessions.")

    click.echo("  Running pattern detectors ...")
    evidence = run_all_detectors(root)
    if evidence:
        click.echo(f"  Found {len(evidence)} detector pattern(s).")

    click.echo("  Generating semantic layer via Claude API ...")

    try:
        result = enricher.enrich(
            graph, root,
            insights_text=insights_text,
            existing_rules=rules_file.rules,
            detector_evidence=evidence,
            dismissed=dismissed,
        )
    except RuntimeError as e:
        click.echo(f"  Semantic enrichment failed: {e}")
        return

    # Preserve user-defined constraints — never overwritten by AI
    if existing:
        result.layer.constraints = existing.constraints
    sem_store.save(result.layer)
    tokens = result.layer.meta.get("input_tokens", 0) + result.layer.meta.get("output_tokens", 0)
    secs = result.layer.meta.get("duration_s", 0)
    click.echo(
        f"  [ok] Semantic: {len(result.layer.zone_intents)} zones "
        f"({tokens} tokens, {secs}s)"
    )

    audit = result.rules_audit
    if audit.is_empty():
        return

    filtered_audit, dis_adds, dis_removes, dis_updates = _interactive_review(audit, rules_file, yes)

    if dis_adds or dis_removes or dis_updates:
        dismissed_store.merge(dis_adds, dis_removes, dis_updates)

    if not filtered_audit.is_empty():
        added, updated, removed = _apply_audit(rules_file, filtered_audit, rules_store)
        rules_store.save(rules_file)
        compile_overview(rules_file, rules_store.overview_path)
        click.echo(f"  [ok] Rules: +{added} added, {updated} updated, {removed} removed")


def _interactive_review(
    audit: RulesAudit, rules_file: RulesFile, yes: bool
) -> tuple[RulesAudit, list, list[int], list[int]]:
    """Review proposed rule changes one by one.

    Y = accept, n = skip (dismissed), q = accept this and all remaining.
    Returns (filtered_audit, dismissed_adds, dismissed_remove_ids, dismissed_update_ids).
    """
    import sys

    from winkers.conventions import RulesAudit

    if yes or not sys.stdout.isatty():
        return audit, [], [], []

    rules_by_id = {r.id: r for r in rules_file.rules}
    dismissed_adds = []
    dismissed_removes: list[int] = []
    dismissed_updates: list[int] = []
    selected_add = []
    selected_update = []
    selected_remove = []

    total = len(audit.add) + len(audit.update) + len(audit.remove)
    click.echo(f"\n  {total} rule change(s) proposed. Review each  (q = accept rest):")

    quit_all = False

    def _ask(prompt_text: str) -> str:
        return click.prompt(f"  {prompt_text}", default="y", show_default=False).strip().lower()

    def _trunc(s: str, n: int = 200) -> str:
        return s if len(s) <= n else s[:n] + "…"

    for i, r in enumerate(audit.add, 1):
        if quit_all:
            selected_add.append(r)
            continue
        click.echo(f"\n  [{i}/{total}] ADD  [{r.category}]  {r.title}")
        click.echo(f"  content:  {_trunc(r.content)}")
        if r.wrong_approach:
            click.echo(f"  avoid:    {_trunc(r.wrong_approach)}")
        if r.affects:
            click.echo(f"  affects:  {', '.join(r.affects)}")
        choice = _ask("Accept? [Y/n/q]")
        if choice.startswith("q"):
            quit_all = True
            selected_add.append(r)
        elif choice.startswith("n"):
            dismissed_adds.append(r)
        else:
            selected_add.append(r)

    for i, r in enumerate(audit.update, len(audit.add) + 1):
        if quit_all:
            selected_update.append(r)
            continue
        current = rules_by_id.get(r.id)
        click.echo(f"\n  [{i}/{total}] UPDATE  rule #{r.id}"
                   + (f"  [{current.title}]" if current else ""))
        if current and r.content and r.content != current.content:
            click.echo(f"  was:      {_trunc(current.content, 120)}")
            click.echo(f"  now:      {_trunc(r.content, 120)}")
        elif r.content:
            click.echo(f"  content:  {_trunc(r.content, 120)}")
        if current and r.wrong_approach and r.wrong_approach != current.wrong_approach:
            click.echo(f"  avoid→    {_trunc(r.wrong_approach, 120)}")
        if r.reason:
            click.echo(f"  reason:   {_trunc(r.reason, 120)}")
        choice = _ask("Accept? [Y/n/q]")
        if choice.startswith("q"):
            quit_all = True
            selected_update.append(r)
        elif choice.startswith("n"):
            dismissed_updates.append(r.id)
        else:
            selected_update.append(r)

    for i, r in enumerate(audit.remove, len(audit.add) + len(audit.update) + 1):
        if quit_all:
            selected_remove.append(r)
            continue
        current = rules_by_id.get(r.id)
        click.echo(f"\n  [{i}/{total}] REMOVE  rule #{r.id}")
        if current:
            click.echo(f"  title:    {current.title}")
            click.echo(f"  content:  {_trunc(current.content, 120)}")
        if r.reason:
            click.echo(f"  reason:   {_trunc(r.reason, 120)}")
        choice = _ask("Accept removal? [Y/n/q]")
        if choice.startswith("q"):
            quit_all = True
            selected_remove.append(r)
        elif choice.startswith("n"):
            dismissed_removes.append(r.id)
        else:
            selected_remove.append(r)

    if quit_all:
        click.echo("  Accepted all remaining.")

    filtered = RulesAudit(add=selected_add, update=selected_update, remove=selected_remove)
    return filtered, dismissed_adds, dismissed_removes, dismissed_updates


def _apply_audit(
    rules_file: RulesFile, audit: RulesAudit, store: RulesStore
) -> tuple[int, int, int]:
    """Apply audit to rules_file in-place. Returns (added, updated, removed)."""
    from datetime import date

    from winkers.conventions import ConventionRule

    today = date.today().isoformat()
    added = 0
    for item in audit.add:
        rules_file.rules.append(ConventionRule(
            id=store.next_id(rules_file),
            category=item.category,
            title=item.title,
            content=item.content,
            wrong_approach=item.wrong_approach,
            affects=item.affects,
            related=item.related,
            source="semantic-agent",
            created=today,
        ))
        added += 1

    updated = 0
    for item in audit.update:
        for rule in rules_file.rules:
            if rule.id == item.id:
                if item.title:
                    rule.title = item.title
                if item.content:
                    rule.content = item.content
                if item.wrong_approach:
                    rule.wrong_approach = item.wrong_approach
                updated += 1
                break

    protected = {"manual", "migrated-from-semantic"}
    remove_ids = {
        item.id for item in audit.remove
        if not any(r.id == item.id and r.source in protected for r in rules_file.rules)
    }
    before = len(rules_file.rules)
    rules_file.rules = [r for r in rules_file.rules if r.id not in remove_ids]
    removed = before - len(rules_file.rules)

    return added, updated, removed



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

    if total == 0:
        click.echo("  [ok] Tech debt: clean")
    else:
        click.echo(
            f"  Tech debt: {total} issues "
            f"({high} high, {medium} medium) -> .winkers/debt.json"
        )


def _autodetect_ide(root: Path) -> None:
    """Detect IDE from project files and auto-register MCP server."""
    detected = False

    # Claude Code: .claude/ directory or CLAUDE.md
    if (root / ".claude").is_dir() or (root / "CLAUDE.md").exists():
        _install_claude_code(root)
        detected = True

    # Cursor: .cursor/ directory
    if (root / ".cursor").is_dir():
        _install_cursor(root)
        detected = True

    if not detected:
        click.echo(
            "  No IDE detected. To register manually:\n"
            "    winkers init  (with .claude/ or .cursor/ present)"
        )


def _update_gitignore(root: Path) -> None:
    """Add .winkers/ to project .gitignore if not already present."""
    gitignore = root / ".gitignore"
    entry = ".winkers/"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if entry in content:
            return
        gitignore.write_text(content.rstrip() + f"\n{entry}\n", encoding="utf-8")
    else:
        gitignore.write_text(f"{entry}\n", encoding="utf-8")
    click.echo(f"  [ok] Added {entry} to .gitignore")


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "templates"


def _install_claude_code(root: Path) -> None:
    import shutil as _shutil

    # Remove old project-level MCP config if it exists
    old_settings = root / ".claude" / "settings.json"
    if old_settings.exists():
        old_settings.unlink()
        click.echo("  [ok] Removed old project-level .claude/settings.json")

    # MCP settings -- user scope only (~/.claude.json)
    claude_json = Path.home() / ".claude.json"
    settings: dict = {}
    if claude_json.exists():
        import json as _json
        try:
            settings = _json.loads(claude_json.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    winkers_bin = _shutil.which("winkers") or "winkers"
    settings.setdefault("mcpServers", {})["winkers"] = {
        "command": winkers_bin,
        "args": ["serve", str(root)],
        "type": "stdio",
    }
    claude_json.write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )
    click.echo(f"  [ok] MCP server registered (user scope): {claude_json}")

    _install_session_hook(root, winkers_bin)
    _install_claude_md_snippet(root)


WINKERS_TOOLS_PERMISSION = "mcp__winkers__*"


def _install_claude_md_snippet(root: Path) -> None:
    """Append Winkers MCP usage instructions to CLAUDE.md if not present."""
    claude_md = root / "CLAUDE.md"
    snippet = (_templates_dir() / "claude_code" / "claude_md_snippet.md").read_text(
        encoding="utf-8"
    )
    marker = "## Architectural context (Winkers)"

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if marker in existing:
            click.echo("  [ok] CLAUDE.md already has Winkers section.")
            return
        claude_md.write_text(
            existing.rstrip() + "\n\n" + snippet, encoding="utf-8"
        )
    else:
        claude_md.write_text(snippet, encoding="utf-8")

    click.echo("  [ok] Added Winkers MCP instructions to CLAUDE.md")


def _install_session_hook(root: Path, winkers_bin: str) -> None:
    """Register SessionEnd hook + tool permissions in .claude/settings.json."""
    settings_path = root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    changed = False

    # --- SessionEnd hook ---
    hooks = settings.setdefault("hooks", {})
    session_end = hooks.setdefault("SessionEnd", [])

    # Use forward slashes: Claude Code hooks run in Git Bash on Windows
    hook_bin = winkers_bin.replace("\\", "/")

    # --- record hook ---
    record_exists = any(
        "record" in hook.get("command", "")
        for entry in session_end
        for hook in entry.get("hooks", [])
    )
    if record_exists:
        click.echo("  [ok] SessionEnd record hook already installed.")
    else:
        session_end.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"{hook_bin} record --hook",
                "timeout": 60,
            }],
        })
        click.echo("  [ok] SessionEnd record hook installed.")
        changed = True

    # --- autocommit hook ---
    autocommit_cmd = (
        "git add -A && git diff --cached --quiet"
        " || git commit -m 'wip: auto-commit from Claude session'"
        " --no-verify"
    )
    autocommit_exists = any(
        "auto-commit" in hook.get("command", "")
        for entry in session_end
        for hook in entry.get("hooks", [])
    )
    if autocommit_exists:
        click.echo("  [ok] SessionEnd autocommit hook already installed.")
    else:
        session_end.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": autocommit_cmd,
                "timeout": 30,
            }],
        })
        click.echo("  [ok] SessionEnd autocommit hook installed.")
        changed = True

    # --- Tool permissions ---
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])

    if WINKERS_TOOLS_PERMISSION not in allow:
        allow.append(WINKERS_TOOLS_PERMISSION)
        click.echo(f"  [ok] Tool permissions added: {WINKERS_TOOLS_PERMISSION}")
        changed = True
    else:
        click.echo("  [ok] Tool permissions already set.")

    if changed:
        settings_path.write_text(
            json.dumps(settings, indent=2), encoding="utf-8",
        )


def _install_cursor(root: Path) -> None:
    import shutil
    templates = _templates_dir() / "cursor"
    rules_dst = root / ".cursor" / "rules"
    rules_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(templates / "winkers.mdc", rules_dst / "winkers.mdc")
    click.echo(f"  [ok] Cursor rules installed: {rules_dst / 'winkers.mdc'}")


def _install_generic(root: Path) -> None:
    templates = _templates_dir() / "generic"
    snippet = (templates / "AGENTS.md").read_text(encoding="utf-8")
    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        existing = agents_md.read_text(encoding="utf-8")
        if "Winkers" not in existing:
            agents_md.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
            click.echo(f"  [ok] Appended Winkers snippet to {agents_md}")
        else:
            click.echo("  ~ AGENTS.md already mentions Winkers, skipped.")
    else:
        agents_md.write_text(snippet, encoding="utf-8")
        click.echo(f"  [ok] Created {agents_md}")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--transcript", type=click.Path(exists=True), default=None,
              help="Path to transcript.jsonl file.")
@click.option("--hook", is_flag=True, default=False,
              help="Read Claude Code hook JSON from stdin (SessionEnd).")
@click.option("--catch-up", "catch_up", is_flag=True, default=False,
              help="Find and record all unrecorded sessions.")
def record(path: str, transcript: str | None, hook: bool, catch_up: bool):
    """Record an agent session for learning.

    Parses Claude Code transcript, binds to git commit, computes
    tech debt delta, and scores the session. Results are saved to
    .winkers/sessions/.

    \b
    Modes:
      winkers record                  Default: catch-up (find unrecorded sessions)
      winkers record --hook           Called by Claude Code SessionEnd hook (stdin)
      winkers record --transcript F   Record a specific transcript.jsonl file
      winkers record --catch-up       Explicit catch-up scan

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
    elif catch_up:
        _record_catch_up(root)
    else:
        # Default: catch-up
        _record_catch_up(root)


def _record_from_hook(root: Path) -> None:
    """Read hook JSON from stdin, extract transcript_path, record it."""
    import sys
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
    from winkers.store import GraphStore

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


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--all", "analyze_all", is_flag=True, default=False,
              help="Analyze all unanalyzed sessions.")
def analyze(path: str, analyze_all: bool):
    """Analyze recorded sessions to find knowledge gaps.

    Sends session trace + semantic.json to Haiku (~$0.01/session).
    Results accumulate in .winkers/insights.json.

    \b
    Requires ANTHROPIC_API_KEY.
    """
    root = Path(path).resolve()
    _load_dotenv(root)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        click.echo(
            "Error: ANTHROPIC_API_KEY not set. "
            "Required for session analysis.",
            err=True,
        )
        return

    from winkers.insights_store import InsightsStore
    from winkers.session_store import SessionStore

    session_store = SessionStore(root)
    insights_store = InsightsStore(root)
    sessions = session_store.load_all()

    if not sessions:
        click.echo("No recorded sessions. Run: winkers record")
        return

    # Filter to unanalyzed sessions
    analyzed_ids = {
        sid
        for i in insights_store.load()
        for sid in i.session_ids
    }

    if analyze_all:
        targets = [s for s in sessions if s.session.session_id not in analyzed_ids]
    else:
        # Default: latest unanalyzed, or latest overall
        unanalyzed = [
            s for s in sessions if s.session.session_id not in analyzed_ids
        ]
        targets = unanalyzed[-1:] if unanalyzed else sessions[-1:]

    if not targets:
        click.echo("All sessions already analyzed.")
        return

    semantic_json = _load_semantic_json(root)

    for scored in targets:
        _analyze_one(scored, semantic_json, insights_store, api_key)

    open_count = len(insights_store.open_insights())
    click.echo(f"  Total open insights: {open_count}")


def _load_semantic_json(root: Path) -> str:
    """Load semantic.json as text, or return empty marker."""
    sem_path = root / ".winkers" / "semantic.json"
    if sem_path.exists():
        return sem_path.read_text(encoding="utf-8")
    return "{}"


def _analyze_one(scored, semantic_json: str, insights_store, api_key: str) -> None:
    """Analyze a single scored session."""
    from winkers.analyzer import analyze_session

    sid = scored.session.session_id[:8]
    task = scored.session.task_prompt[:40]
    click.echo(f"  Analyzing {sid} ({task}...) ...")

    try:
        result = analyze_session(scored, semantic_json, api_key=api_key)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        return

    insights_store.merge(result)
    n = len(result.insights)
    tokens = result.input_tokens + result.output_tokens
    click.echo(
        f"  [ok] {n} insight(s) found "
        f"({tokens} tokens, {result.duration_s}s)"
    )


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def insights(path: str):
    """View accumulated knowledge gaps.

    Shows open insights sorted by priority.
    """
    root = Path(path).resolve()

    from winkers.insights_store import InsightsStore

    store = InsightsStore(root)
    items = store.open_insights()

    if not items:
        click.echo("No open insights.")
        return

    total_turns = sum(i.turns_wasted for i in items)
    total_tokens = sum(i.tokens_wasted for i in items)

    click.echo(
        f"Open insights: {len(items)} "
        f"({total_turns} turns wasted, ~{total_tokens} tokens)\n"
    )

    for idx, item in enumerate(items):
        tag = item.priority.upper()
        occ = f"x{item.occurrences}" if item.occurrences > 1 else ""
        click.echo(
            f"  [{idx}] [{tag}]{occ} {item.category}: "
            f"{item.description}"
        )
        click.echo(
            f"       -> {item.semantic_target}: "
            f"{item.injection_content}"
        )
        click.echo()


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def improve(path: str):
    """Show what insights will feed into the next semantic enrichment.

    \b
    Insights come from  winkers analyze  and accumulate in insights.json.
    High-priority and repeated insights are included in the prompt when
    you run  winkers init  -- the model weaves them into semantic.json.

    \b
    This command is read-only. To apply insights, re-run  winkers init.
    """
    root = Path(path).resolve()

    from winkers.insights_store import InsightsStore

    store = InsightsStore(root)
    all_open = store.open_insights()

    if not all_open:
        click.echo("No open insights. Nothing to improve.")
        return

    qualifying = [
        i for i in all_open
        if i.priority == "high"
        or (i.priority == "medium" and i.occurrences >= 2)
    ]

    total_turns = sum(i.turns_wasted for i in all_open)
    total_tokens = sum(i.tokens_wasted for i in all_open)

    click.echo(
        f"Insights: {len(all_open)} open, "
        f"{len(qualifying)} qualify for next init\n"
        f"Evidence: {total_turns} turns wasted, "
        f"~{total_tokens} tokens\n"
    )

    # Show by target
    by_target: dict[str, list] = {}
    for item in qualifying:
        by_target.setdefault(item.semantic_target, []).append(item)

    for target, group in sorted(by_target.items()):
        click.echo(f"  semantic.json -> {target}:")
        for item in group:
            occ = f" ({item.occurrences} sessions)" if item.occurrences > 1 else ""
            click.echo(f"    + {item.injection_content}{occ}")
        click.echo()

    # Show non-qualifying
    pending = [i for i in all_open if i not in qualifying]
    if pending:
        click.echo(
            f"  {len(pending)} more insight(s) pending "
            f"(need higher priority or more occurrences)"
        )
        click.echo()

    click.echo("To apply: run  winkers init  (includes insights in prompt)")


@cli.command("conventions-migrate")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Accept all entries without interactive review.")
def conventions_migrate(path: str, yes: bool):
    """Migrate conventions/constraints from old semantic.json to rules.json.

    For projects that ran  winkers init  before v0.7.0, semantic.json may
    contain  conventions[]  and  constraints[]  fields.  This command reads
    them and imports them as rules with source 'migrated-from-semantic'.

    Safe to run multiple times — already-imported entries are skipped.
    """
    import json as _json
    from datetime import date

    from winkers.conventions import (
        ConventionRule,
        RulesStore,
        compile_overview,
    )
    from winkers.store import STORE_DIR

    root = Path(path).resolve()
    semantic_path = root / STORE_DIR / "semantic.json"

    if not semantic_path.exists():
        click.echo("No semantic.json found. Nothing to migrate.")
        return

    raw = _json.loads(semantic_path.read_text(encoding="utf-8"))
    entries: list[str] = []
    for field in ("conventions", "constraints"):
        val = raw.get(field)
        if isinstance(val, list):
            for v in val:
                if isinstance(v, dict):
                    text = v.get("content") or v.get("text") or v.get("rule") or ""
                    if text:
                        entries.append(str(text))
                elif v:
                    entries.append(str(v))

    if not entries:
        click.echo(
            "semantic.json has no 'conventions' or 'constraints' fields. Nothing to migrate."
        )
        return

    click.echo(f"Found {len(entries)} entries in semantic.json to migrate.\n")

    rules_store = RulesStore(root)
    rules_file = rules_store.load()

    # Skip entries that are already in rules.json (same content)
    existing_contents = {r.content for r in rules_file.rules}
    new_entries = [e for e in entries if e not in existing_contents]
    skipped_existing = len(entries) - len(new_entries)
    if skipped_existing:
        click.echo(f"  {skipped_existing} already imported — skipped.\n")

    if not new_entries:
        click.echo("All entries already in rules.json.")
        return

    today = date.today().isoformat()
    accepted = 0

    for idx, content in enumerate(new_entries, 1):
        click.echo(f"[{idx}/{len(new_entries)}] {content}")
        if yes:
            do_accept = True
        else:
            choice = click.prompt("  Accept? [y/n]", default="y")
            do_accept = choice.lower().startswith("y")

        if do_accept:
            rule = ConventionRule(
                id=rules_store.next_id(rules_file),
                category="architecture",
                title=content[:60].rstrip(),
                content=content,
                source="migrated-from-semantic",
                created=today,
            )
            rules_file.rules.append(rule)
            accepted += 1
        else:
            click.echo("  Skipped.")

    if accepted:
        rules_store.save(rules_file)
        compile_overview(rules_file, rules_store.overview_path)
        click.echo(f"\n[ok] Migrated {accepted} rule(s) to .winkers/rules/rules.json")
        click.echo("     overview.md updated.")
    else:
        click.echo("\nNo rules accepted.")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def serve(path: str):
    """Start the MCP server (stdio). AI agents connect here."""
    from winkers.mcp.server import run
    root = Path(path).resolve()
    run(root)


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--port", default=7420, show_default=True, help="HTTP port")
@click.option("--no-browser", is_flag=True, default=False, help="Don't open browser")
def dashboard(path: str, port: int, no_browser: bool):
    """Open the browser dependency graph.

    Rebuilds the graph before opening to ensure fresh data.
    """
    import webbrowser

    from winkers.dashboard.api import run as run_dashboard

    root = Path(path).resolve()

    # Rebuild graph for fresh data
    click.echo("Rebuilding graph ...")
    builder = GraphBuilder()
    graph = builder.build(root)
    CrossFileResolver().resolve(graph, str(root))
    _collect_git_history(root, graph)
    store = GraphStore(root)
    store.save(graph)
    _save_history_snapshot(root, graph)
    click.echo(
        f"  {len(graph.files)} files, {len(graph.functions)} functions, "
        f"{len(graph.call_edges)} edges"
    )

    url = f"http://127.0.0.1:{port}"
    click.echo(f"Dashboard at {url}")
    if not no_browser:
        webbrowser.open(url)
    run_dashboard(root, port=port)




