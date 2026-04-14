"""Winkers CLI."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from winkers.conventions import RulesAudit, RulesFile, RulesStore

from winkers import __version__
from winkers.git import AUTO_COMMIT_MARKER
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
      2. winkers init           Build graph + semantic + register MCP
      3. winkers doctor         Verify everything is set up
      4. winkers dashboard      Open browser graph

    \b
    Improve loop (learn from agent sessions):
      winkers record            Record unrecorded sessions
      winkers analyze           Find knowledge gaps via Haiku
      winkers improve           Show insights (--apply to inject)

    \b
    Project protection:
      winkers protect --startup Trace startup import chain
      winkers hooks             Install commit format + git hooks
      winkers commits --enrich  AI-powered commit message enrichment

    \b
    Recording + autocommit hooks are installed automatically by init.
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
@click.option("--force", "-f", is_flag=True, default=False,
              help="Force semantic re-enrichment even if graph is unchanged.")
def init(path: str, no_semantic: bool, yes: bool, force: bool):
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

    _repair_sessions(root)
    _run_debt_analysis(root, graph)

    if not no_semantic:
        _run_semantic_enrichment(root, graph, yes=yes, force=force)

    _autodetect_ide(root)


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


def _repair_sessions(root: Path) -> None:
    """Fix mojibake in commit messages caused by missing encoding='utf-8'."""
    import json

    sessions_dir = root / ".winkers" / "sessions"
    if not sessions_dir.exists():
        return

    fixed = 0
    for path in sessions_dir.glob("*.json"):
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            continue

        msg = data.get("commit", {}).get("message")
        if not msg:
            continue

        try:
            repaired = msg.encode("cp1251").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue

        if repaired != msg:
            data["commit"]["message"] = repaired
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            fixed += 1

    if fixed:
        click.echo(f"  [ok] Repaired {fixed} session(s) with garbled commit messages")


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


def _backup_file(src: Path, history_dir: Path, prefix: str) -> None:
    """Copy src to history_dir/<prefix>-<timestamp>.json before overwriting."""
    import shutil
    from datetime import datetime

    if not src.exists():
        return
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    shutil.copy2(src, history_dir / f"{prefix}-{ts}.json")
    snapshots = sorted(history_dir.glob(f"{prefix}-*.json"))
    if len(snapshots) > MAX_SNAPSHOTS:
        for old in snapshots[:-MAX_SNAPSHOTS]:
            old.unlink()


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


def _run_semantic_enrichment(root: Path, graph, yes: bool = False, force: bool = False) -> None:
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
        from winkers.semantic import SemanticEnricher, SemanticStore
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

    if existing and not enricher.is_stale(graph, root, existing) and not force:
        click.echo("  Semantic data up to date, skipping API call.")
        return

    click.echo("  Running pattern detectors ...")
    evidence = run_all_detectors(root)
    if evidence:
        click.echo(f"  Found {len(evidence)} detector pattern(s).")

    click.echo("  Generating semantic layer via Claude API ...")

    try:
        result = enricher.enrich(
            graph, root,
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
    _backup_file(sem_store.semantic_path, root / ".winkers" / "history", "semantic")
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
        _backup_file(rules_store.rules_path, root / ".winkers" / "history", "rules")
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
    """Add .winkers/ and .mcp.json to project .gitignore if not already present."""
    gitignore = root / ".gitignore"
    entries = [".winkers/", ".mcp.json"]

    existing = ""
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")

    added = []
    for entry in entries:
        if entry not in existing:
            added.append(entry)

    if not added:
        return

    block = "\n".join(added) + "\n"
    new_content = existing.rstrip() + "\n" + block if existing else block
    gitignore.write_text(new_content, encoding="utf-8")
    click.echo(f"  [ok] Added {', '.join(added)} to .gitignore")


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "templates"


def _install_claude_code(root: Path) -> None:
    import shutil as _shutil

    # --- Project-level .mcp.json ---
    # Use absolute path so the MCP server finds the project
    # regardless of the working directory Claude Code launches from.
    mcp_json = root / ".mcp.json"
    root_posix = str(root).replace("\\", "/")
    mcp_config = {
        "mcpServers": {
            "winkers": {
                "command": "uvx",
                "args": ["winkers", "serve", root_posix],
                "type": "stdio",
            }
        }
    }
    mcp_json.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
    click.echo(f"  [ok] MCP server registered (project): {mcp_json}")

    # --- Clean up old user-scope registration if present ---
    _remove_user_scope_mcp()

    winkers_bin = _shutil.which("winkers") or "winkers"
    _install_session_hook(root, winkers_bin)
    _install_claude_md_snippet(root)
    _install_semantic_summary(root)


def _remove_user_scope_mcp() -> None:
    """Remove stale winkers entry from ~/.claude.json (migrated to .mcp.json)."""
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return
    try:
        settings = json.loads(claude_json.read_text(encoding="utf-8"))
    except Exception:
        return
    servers = settings.get("mcpServers", {})
    if "winkers" not in servers:
        return
    del servers["winkers"]
    if not servers:
        settings.pop("mcpServers", None)
    claude_json.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    click.echo(f"  [ok] Removed old user-scope MCP entry from {claude_json}")


WINKERS_TOOLS_PERMISSION = "mcp__winkers__*"


def _install_claude_md_snippet(root: Path) -> None:
    """Append (or update) Winkers MCP usage instructions in CLAUDE.md."""
    claude_md = root / "CLAUDE.md"
    snippet = (_templates_dir() / "claude_code" / "claude_md_snippet.md").read_text(
        encoding="utf-8"
    )
    marker = "## Architectural context (Winkers)"
    version_pattern = re.compile(r"<!-- winkers-snippet-version: ([\d.]+) -->")

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if marker in existing:
            m = version_pattern.search(existing)
            if m and m.group(1) == __version__:
                click.echo("  [ok] CLAUDE.md Winkers section is up to date.")
                return
            # Replace the old snippet block with the new one
            start = existing.index(marker)
            # version comment sits on the line before the marker
            comment_start = existing.rfind("\n", 0, start)
            block_start = (comment_start + 1) if comment_start != -1 else start
            rest = existing[start + len(marker):]
            next_heading = re.search(r"\n## ", rest)
            if next_heading:
                end = start + len(marker) + next_heading.start()
                updated = existing[:block_start] + snippet + "\n" + existing[end + 1:]
            else:
                updated = existing[:block_start] + snippet
            claude_md.write_text(updated.rstrip() + "\n", encoding="utf-8")
            click.echo(f"  [ok] Updated Winkers section in CLAUDE.md to v{__version__}.")
            return
        # Insert after first heading (# Title) so agent reads it early.
        # Fall back to prepending if no heading found.
        first_h1 = re.search(r"^# .+\n", existing, re.MULTILINE)
        if first_h1:
            insert_at = first_h1.end()
            updated = existing[:insert_at] + "\n" + snippet + "\n" + existing[insert_at:]
        else:
            updated = snippet + "\n\n" + existing
        claude_md.write_text(updated.rstrip() + "\n", encoding="utf-8")
    else:
        claude_md.write_text(snippet, encoding="utf-8")

    click.echo("  [ok] Added Winkers MCP instructions to CLAUDE.md")


_SEM_START = "<!-- winkers-semantic-start -->"
_SEM_END = "<!-- winkers-semantic-end -->"


def _install_semantic_summary(root: Path) -> None:
    """Append or update a short semantic summary block in CLAUDE.md.

    Reads semantic.json and writes ~200 tokens of data_flow, domain_context,
    and constraints so the agent has project context before its first tool call.
    """
    from winkers.semantic import SemanticStore

    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        return

    sem = SemanticStore(root).load()
    if sem is None:
        return

    lines: list[str] = []
    if sem.data_flow:
        lines.append(f"- **Data flow**: {sem.data_flow}")
    if sem.domain_context:
        lines.append(f"- **Domain**: {sem.domain_context}")
    if sem.constraints:
        for c in sem.constraints[:3]:
            lines.append(f"- **Constraint**: {c}")

    if not lines:
        return

    block = (
        f"{_SEM_START}\n"
        "### Project context (auto-generated)\n\n"
        + "\n".join(lines) + "\n"
        f"{_SEM_END}"
    )

    existing = claude_md.read_text(encoding="utf-8")

    if _SEM_START in existing:
        start = existing.index(_SEM_START)
        end = existing.index(_SEM_END) + len(_SEM_END)
        updated = existing[:start] + block + existing[end:]
        claude_md.write_text(updated, encoding="utf-8")
    else:
        claude_md.write_text(
            existing.rstrip() + "\n\n" + block + "\n", encoding="utf-8"
        )
    click.echo("  [ok] Updated CLAUDE.md with semantic summary")


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

    # --- autocommit hook (must run before record so bind_to_commit finds the commit) ---
    autocommit_cmd = (
        f"git add -A && git diff --cached --quiet"
        f" || {hook_bin} autocommit"
    )
    autocommit_exists = any(
        AUTO_COMMIT_MARKER in hook.get("command", "")
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


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--apply", "do_apply", is_flag=True, default=False,
              help="Inject high-priority insights into semantic.json.")
def improve(path: str, do_apply: bool):
    """Show or apply accumulated insights from session analysis.

    \b
    Default (dry-run): show open insights grouped by priority.
    --apply: inject high-priority injection_content into
             semantic.json constraints[], backup, mark as fixed.
    """
    root = Path(path).resolve()

    from winkers.insights_store import InsightsStore
    insights_store = InsightsStore(root)
    open_items = insights_store.open_insights()

    if not open_items:
        click.echo("No open insights. Run: winkers analyze")
        return

    # Display insights
    for i, item in enumerate(open_items):
        marker = "*" if item.priority == "high" else "-"
        occ = f" (x{item.occurrences})" if item.occurrences > 1 else ""
        click.echo(
            f"  {marker} [{item.priority}] {item.category}{occ}: "
            f"{item.description}"
        )
        click.echo(f"    -> {item.injection_content}")

    high = [it for it in open_items if it.priority == "high"]
    click.echo(
        f"\n{len(open_items)} open insight(s), {len(high)} high-priority."
    )

    if not do_apply:
        click.echo("Run with --apply to inject high-priority insights.")
        return

    if not high:
        click.echo("No high-priority insights to apply.")
        return

    from winkers.semantic import SemanticStore
    sem_store = SemanticStore(root)
    sem = sem_store.load()
    if sem is None:
        click.echo("No semantic.json. Run: winkers init")
        return

    # Backup before modifying
    _backup_file(sem_store.semantic_path, root / ".winkers" / "history", "semantic")

    applied_indices: list[int] = []
    for i, item in enumerate(open_items):
        if item.priority != "high":
            continue
        if item.injection_content and item.injection_content not in sem.constraints:
            sem.constraints.append(item.injection_content)
        applied_indices.append(i)

    sem_store.save(sem)
    insights_store.mark_fixed(applied_indices)

    click.echo(
        f"Applied {len(applied_indices)} insight(s) to semantic.json constraints."
    )


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def doctor(path: str):
    """Check environment and project health."""
    import shutil
    import subprocess
    import sys

    root = Path(path).resolve()
    ok_count = 0
    warn_count = 0

    def ok(msg: str) -> None:
        nonlocal ok_count
        click.echo(f"  [ok] {msg}")
        ok_count += 1

    def warn(msg: str) -> None:
        nonlocal warn_count
        click.echo(f"  [!!] {msg}")
        warn_count += 1

    # ── Environment ──────────────────────────────────────────────
    click.echo("\n  Environment:")

    # Python version
    v = sys.version_info
    if v >= (3, 11):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        warn(f"Python {v.major}.{v.minor} — requires 3.11+")

    # Isolation: where is winkers running from?
    winkers_path = Path(__file__).resolve()
    exe_path = Path(sys.executable).resolve()
    in_pipx = "pipx" in str(winkers_path).lower()
    in_project_venv = (root / ".venv").exists() and str(root.resolve()) in str(exe_path)

    if in_pipx:
        ok("Running from pipx (isolated)")
    elif in_project_venv:
        warn("Running from project .venv — may conflict with project deps")
    else:
        ok(f"Running from: {exe_path.parent}")

    # tree-sitter
    try:
        import tree_sitter  # noqa: F401
        ok("tree-sitter installed")
    except ImportError:
        warn("tree-sitter not installed")

    # Grammars
    grammars = [
        "tree_sitter_python", "tree_sitter_javascript", "tree_sitter_typescript",
        "tree_sitter_java", "tree_sitter_go", "tree_sitter_rust", "tree_sitter_c_sharp",
    ]
    missing_grammars = []
    for g in grammars:
        try:
            __import__(g)
        except ImportError:
            missing_grammars.append(g)
    if missing_grammars:
        warn(f"Missing grammars: {', '.join(missing_grammars)}")
    else:
        ok(f"All {len(grammars)} language grammars installed")

    # git
    if shutil.which("git"):
        ok("git available")
    else:
        warn("git not found in PATH")

    # anthropic (optional)
    try:
        import anthropic
        ok(f"anthropic {anthropic.__version__}")
    except ImportError:
        warn("anthropic not installed (install with: pip install winkers[semantic])")

    # API key
    _load_dotenv(root)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        ok(f"ANTHROPIC_API_KEY set ({api_key[:12]}...)")
    else:
        warn("ANTHROPIC_API_KEY not set (semantic features disabled)")

    # ── Project files ────────────────────────────────────────────
    click.echo("\n  Project:")

    # graph.json
    from winkers.store import GraphStore
    graph = GraphStore(root).load()
    if graph:
        ok(
            f"graph.json: {len(graph.files)} files, "
            f"{len(graph.functions)} functions, "
            f"{len(graph.call_edges)} call edges"
        )
    else:
        warn("No graph.json — run: winkers init")

    # Schema version
    if graph and graph.meta.get("schema_version"):
        ok(f"Schema version: {graph.meta['schema_version']}")
    elif graph:
        warn("No schema_version in graph — run: winkers init to rebuild")

    # semantic.json
    from winkers.semantic import SemanticStore
    sem = SemanticStore(root).load()
    if sem:
        ok(f"semantic.json: {len(sem.zone_intents)} zone intents")
    else:
        warn("No semantic.json — run: winkers init (needs API key)")

    # rules.json
    from winkers.conventions import RulesStore
    rules = RulesStore(root).load()
    if rules.rules:
        ok(f"rules.json: {len(rules.rules)} rules")
    else:
        warn("No rules — run: winkers init (with API key for auto-detection)")

    # ── IDE integration ──────────────────────────────────────────
    click.echo("\n  IDE integration:")

    # MCP registration
    mcp_json = root / ".mcp.json"
    if mcp_json.exists():
        ok("MCP registered (.mcp.json)")
    else:
        warn("No .mcp.json — run: winkers init (with .claude/ present)")

    # CLAUDE.md snippet
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        import winkers
        version_marker = f"winkers-snippet-version: {winkers.__version__}"
        if version_marker in content:
            ok(f"CLAUDE.md snippet up to date (v{winkers.__version__})")
            # Check position: snippet should be in the first half
            marker_pos = content.index("Architectural context (Winkers)")
            if len(content) > 0 and marker_pos > len(content) // 2:
                warn(
                    "CLAUDE.md Winkers section is near the end"
                    " — run: winkers init to move it up"
                )
            else:
                ok("CLAUDE.md Winkers section positioned early")
        elif "winkers-snippet-version" in content:
            warn("CLAUDE.md snippet outdated — run: winkers init")
        else:
            warn("CLAUDE.md exists but no Winkers snippet")

        if "<!-- winkers-semantic-start -->" in content:
            ok("CLAUDE.md semantic summary present")
        elif sem:
            warn("CLAUDE.md missing semantic summary — run: winkers init")
    else:
        warn("No CLAUDE.md")

    # SessionEnd hook
    settings_path = root / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if "SessionEnd" in settings.get("hooks", {}):
                ok("SessionEnd hook configured")
            else:
                warn("No SessionEnd hook in .claude/settings.json")
        except Exception:
            warn("Could not read .claude/settings.json")
    elif (root / ".claude").is_dir():
        warn("No .claude/settings.json — run: winkers init")

    # ── Protect & hooks ──────────────────────────────────────────
    click.echo("\n  Features:")

    # Protect config
    from winkers.protect import load_startup_chain
    chain = load_startup_chain(root)
    if chain:
        ok(f"Startup chain: {len(chain)} protected files")
    else:
        ok("No startup chain configured (optional: winkers protect --startup)")

    # Commit format
    from winkers.commit_format import load_commit_format
    fmt = load_commit_format(root)
    if fmt:
        ok(f"Commit format: {fmt.get('template', '?')}")
    else:
        ok("No commit format configured (optional: winkers hooks)")

    # Git hooks
    hook_path = root / ".githooks" / "prepare-commit-msg"
    if hook_path.exists():
        try:
            hooks_path_setting = subprocess.check_output(
                ["git", "config", "core.hooksPath"],
                text=True, cwd=str(root), stderr=subprocess.DEVNULL,
            ).strip()
            if hooks_path_setting == ".githooks":
                ok("Git hooksPath = .githooks")
            else:
                warn(f"Git hooksPath = '{hooks_path_setting}', expected '.githooks'")
        except Exception:
            warn(
                "Hook installed but core.hooksPath not set"
                " — run: git config core.hooksPath .githooks"
            )
    elif fmt:
        warn("Commit format set but hook not installed — run: winkers hooks")

    # ── Sessions & insights ──────────────────────────────────────
    click.echo("\n  Learning:")

    from winkers.session_store import SessionStore
    sessions = SessionStore(root).load_all()
    if sessions:
        ok(f"{len(sessions)} recorded session(s)")
    else:
        ok("No recorded sessions (run: winkers record)")

    from winkers.insights_store import InsightsStore
    insights = InsightsStore(root).open_insights()
    if insights:
        high = sum(1 for i in insights if i.priority == "high")
        ok(f"{len(insights)} open insight(s) ({high} high-priority)")
    elif sessions:
        ok("No insights (run: winkers analyze)")
    else:
        ok("No insights yet")

    # ── Summary ──────────────────────────────────────────────────
    click.echo(f"\n  {ok_count} ok, {warn_count} warning(s)")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--startup", is_flag=True, default=False,
              help="Detect entry point and trace import chain.")
@click.option("--entry", default=None,
              help="Override entry point file (e.g. app.py).")
def protect(path: str, startup: bool, entry: str | None):
    """Mark critical files that must not break.

    \b
    winkers protect --startup          Auto-detect entry point
    winkers protect --startup --entry app.py   Use specific entry
    """
    if not startup:
        click.echo("Use --startup to trace the startup import chain.")
        return

    root = Path(path).resolve()

    from winkers.protect import (
        detect_entry_point,
        save_protect_config,
        trace_startup_chain,
    )
    from winkers.store import GraphStore

    graph = GraphStore(root).load()
    if graph is None:
        click.echo("No graph.json. Run: winkers init")
        return

    if entry is None:
        entry = detect_entry_point(graph)
    if entry is None:
        click.echo(
            "No entry point found. Use --entry to specify "
            "(e.g. winkers protect --startup --entry app.py)"
        )
        return

    if entry not in graph.files:
        click.echo(f"Entry point '{entry}' not found in graph.")
        return

    chain = trace_startup_chain(graph, entry)
    save_protect_config(root, entry, chain)
    click.echo(
        f"  [ok] Startup chain: {entry} -> {len(chain)} files protected.\n"
        f"  Chain: {', '.join(chain)}"
    )


@cli.command("hooks")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--template", default="[{ticket}] {message}",
              help="Commit message template. Variables: {message}, {ticket}, {date}, {author}.")
@click.option("--ticket-pattern", default=r"[A-Z]+-\d+",
              help="Regex to extract ticket from branch or message.")
def hooks_install(path: str, template: str, ticket_pattern: str):
    """Install git hooks and configure commit format.

    \b
    Installs .githooks/prepare-commit-msg that applies the template.
    Also saves the format in .winkers/config.json.

    \b
    After install, run:
      git config core.hooksPath .githooks
    """
    root = Path(path).resolve()

    from winkers.commit_format import install_hook, save_commit_format

    save_commit_format(root, template, ticket_pattern)
    hook_path = install_hook(root)

    click.echo("  [ok] Commit format saved to .winkers/config.json")
    click.echo(f"  [ok] Hook installed: {hook_path.relative_to(root)}")
    click.echo("  Run: git config core.hooksPath .githooks")


@cli.command("commit-fmt", hidden=True)
@click.argument("msg_file", type=click.Path(exists=True))
def commit_fmt(msg_file: str):
    """Format a commit message file (called by prepare-commit-msg hook)."""
    msg_path = Path(msg_file)
    root = Path(".").resolve()

    from winkers.commit_format import format_message, load_commit_format

    fmt = load_commit_format(root)
    if not fmt:
        return

    template = fmt.get("template", "{message}")
    ticket_pattern = fmt.get("ticket_pattern", r"[A-Z]+-\d+")

    original = msg_path.read_text(encoding="utf-8").strip()
    if not original:
        return

    formatted = format_message(original, template, ticket_pattern)
    msg_path.write_text(formatted + "\n", encoding="utf-8")


@cli.command("autocommit")
@click.argument("path", default=".", type=click.Path(exists=True))
def autocommit(path: str):
    """Generate a commit message via Haiku and commit staged changes.

    \b
    Intended for the SessionEnd hook:
      winkers autocommit

    Generates a meaningful message from the staged diff via Claude API.
    Falls back to file/function list if API is unavailable.
    Applies the configured commit_format template if set.
    """
    import subprocess as _sp

    root = Path(path).resolve()
    _load_dotenv(root)

    # Check there are staged changes
    try:
        _sp.check_output(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(root), stderr=_sp.DEVNULL,
        )
        # exit code 0 = no staged changes
        return
    except _sp.CalledProcessError:
        pass  # exit code 1 = there are staged changes

    from winkers.commit_format import (
        format_message,
        generate_commit_message,
        load_commit_format,
    )

    msg = generate_commit_message(root)

    # Apply template if configured
    fmt = load_commit_format(root)
    if fmt and fmt.get("template"):
        msg = format_message(
            msg,
            fmt["template"],
            fmt.get("ticket_pattern", r"[A-Z]+-\d+"),
        )

    try:
        _sp.check_call(
            ["git", "commit", "-m", msg],
            cwd=str(root),
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
        click.echo(f"  [ok] {msg}")
    except _sp.CalledProcessError:
        click.echo("  [!!] git commit failed", err=True)


@cli.command("commits")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--range", "git_range", default="HEAD~5..HEAD",
              help="Git range (default: HEAD~5..HEAD).")
@click.option("--enrich", is_flag=True, default=False,
              help="Use Haiku to generate better messages for all commits in range.")
@click.option("--dry-run/--apply", default=True,
              help="Show changes without applying (default: dry-run).")
def commits_normalize(path: str, git_range: str, enrich: bool, dry_run: bool):
    """Normalize or enrich commit messages.

    \b
    winkers commits --range HEAD~10..HEAD              Template normalization
    winkers commits --enrich --range HEAD~20..HEAD     AI-powered enrichment
    winkers commits --enrich --apply                   Rewrite with enriched messages
    """
    root = Path(path).resolve()

    if enrich:
        _commits_enrich(root, git_range, dry_run)
    else:
        _commits_template(root, git_range, dry_run)


def _commits_template(root: Path, git_range: str, dry_run: bool) -> None:
    """Normalize commits using the configured template."""
    from winkers.commit_format import load_commit_format, normalize_commits

    fmt = load_commit_format(root)
    if not fmt:
        click.echo("No commit_format in config. Run: winkers hooks")
        return

    results = normalize_commits(root, git_range, dry_run=dry_run)
    if not results:
        click.echo("No commits need normalization.")
        return

    for r in results:
        click.echo(f"  {r['hash']}  {r['old']}")
        click.echo(f"        -> {r['new']}")

    if dry_run:
        click.echo(
            f"\n{len(results)} commit(s) to normalize."
            " Run with --apply to rewrite."
        )


def _commits_enrich(root: Path, git_range: str, dry_run: bool) -> None:
    """Enrich commit messages using Haiku (diff + session context)."""
    import subprocess as _sp

    _load_dotenv(root)

    try:
        log_output = _sp.check_output(
            ["git", "log", "--format=%H|%s|%aI|%an", git_range],
            text=True, cwd=str(root), stderr=_sp.DEVNULL,
        ).strip()
    except Exception:
        click.echo("Could not read git log.")
        return

    if not log_output:
        click.echo("No commits in range.")
        return

    from winkers.commit_format import (
        enrich_commit,
        format_message,
        load_commit_format,
    )

    fmt = load_commit_format(root)
    template = fmt.get("template") if fmt else None
    ticket_pattern = fmt.get("ticket_pattern", r"[A-Z]+-\d+") if fmt else r"[A-Z]+-\d+"

    results = []
    for line in log_output.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commit_hash, old_msg, date, author = parts

        new_msg = enrich_commit(root, commit_hash)
        if new_msg is None:
            continue

        # Apply template if configured
        if template:
            new_msg = format_message(new_msg, template, ticket_pattern)

        if new_msg != old_msg:
            results.append({
                "hash": commit_hash[:8],
                "old": old_msg,
                "new": new_msg,
                "date": date[:10],
                "author": author,
            })

    if not results:
        click.echo("No commits to enrich.")
        return

    for r in results:
        click.echo(f"  {r['hash']}  {r['date']}  {r['author']}")
        click.echo(f"    old: {r['old']}")
        click.echo(f"    new: {r['new']}")

    if dry_run:
        click.echo(
            f"\n{len(results)} commit(s) to enrich."
            " Run with --apply to rewrite."
        )
    else:
        click.echo(
            f"\n{len(results)} commit(s) enriched."
            " Use git rebase -i to apply the new messages."
        )


@cli.command()
@click.argument("intent")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--zone", default="", help="Filter by zone name")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def search(intent: str, path: str, zone: str, as_json: bool):
    """Search project functions by intent.

    \b
    Examples:
        winkers search "calculate price"
        winkers search "validate email" --zone api
        winkers search "parse config" --json
    """
    root = Path(path).resolve()
    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    from winkers.search import (
        _fn_signature,
        format_before_create_response,
        get_pipeline_context,
        search_functions,
    )

    matches = search_functions(graph, intent, zone=zone)

    if as_json:
        result = format_before_create_response(graph, intent, matches, zone=zone, root=root)
        click.echo(json.dumps(result, indent=2))
        return

    if not matches:
        click.echo(f"No functions found matching: {intent}")
        return

    click.echo(f"Found {len(matches)} match(es) for \"{intent}\":\n")
    for i, m in enumerate(matches, 1):
        pipeline = get_pipeline_context(graph, m.fn.id)
        click.echo(f"  {i}. {m.fn.name}() in {m.fn.file}:{m.fn.line_start}")
        click.echo(f"     Signature: {_fn_signature(m.fn)}")
        click.echo(f"     Callers: {m.callers}  Score: {m.score}")
        if m.fn.docstring:
            click.echo(f"     Doc: {m.fn.docstring}")
        if pipeline.upstream:
            for fn in pipeline.upstream:
                click.echo(f"     ↑ {fn.name}{_fn_signature(fn)}  [{fn.file}:{fn.line_start}]")
        if pipeline.downstream:
            for fn in pipeline.downstream:
                click.echo(f"     ↓ {fn.name}{_fn_signature(fn)}  [{fn.file}:{fn.line_start}]")
        click.echo()


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




