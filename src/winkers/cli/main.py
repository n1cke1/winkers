"""Winkers CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from winkers.cli.init_pipeline import (  # noqa: F401  (re-exports for tests/test_hooks.py)
    _apply_audit,
    _author_meta_unit_descriptions,
    _autodetect_ide,
    _backup_file,
    _collect_git_history,
    _detect_and_lock_language,
    _gc_runtime_sessions,
    _install_claude_code,
    _install_claude_md_snippet,
    _install_cursor,
    _install_generic,
    _install_interactive_hooks,
    _install_session_hook,
    _install_winkers_pointer,
    _intent_provider_ready,
    _interactive_review,
    _is_winkers_managed_hook,
    _load_dotenv,
    _migrate_user_scope_mcp,
    _read_fn_source,
    _repair_sessions,
    _run_debt_analysis,
    _run_impact_generation,
    _run_impact_only,
    _run_intent_generation,
    _run_semantic_enrichment,
    _run_units_pipeline,
    _save_history_snapshot,
    _strip_managed_hooks,
    _strip_winkers_snippet,
    _templates_dir,
    _update_gitignore,
    _upsert_hook,
    _value_unit_kind_from_collection,
    _winkers_bin,
)
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


# Subgroups defined in their own modules; register them as `cli` children
# here so `winkers hook ...` and `winkers intent ...` resolve normally.
from winkers.cli.hook_group import hook  # noqa: E402
from winkers.cli.intent_group import intent  # noqa: E402

cli.add_command(hook)
cli.add_command(intent)


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--no-semantic", is_flag=True, default=False,
              help="Skip semantic enrichment (no Claude API call).")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Accept all proposed rule changes without interactive review.")
@click.option("--force", "-f", is_flag=True, default=False,
              help="Force semantic re-enrichment even if graph is unchanged.")
@click.option("--ollama", "ollama_model", default=None, type=str,
              help="Use Ollama for intent generation (e.g. gemma3:4b). "
                   "Saved to .winkers/config.toml for future runs.")
@click.option("--no-llm", is_flag=True, default=False,
              help="Skip LLM intent generation.")
@click.option("--no-impact", is_flag=True, default=False,
              help="Skip pre-computed impact analysis (intent still runs).")
@click.option("--impact-only", is_flag=True, default=False,
              help="Only run impact analysis; skip graph/semantic/rules rebuild.")
@click.option("--force-impact", is_flag=True, default=False,
              help="Rebuild impact.json even if content_hash is unchanged.")
@click.option("--with-units", is_flag=True, default=False,
              help="Run description-first units pipeline: per-fn/per-section "
                   "descriptions, coupling detection, BGE-M3 embeddings. "
                   "Sequential `claude --print` calls, ~30s per unit. "
                   "Off by default — opt in.")
@click.option("--force-units", is_flag=True, default=False,
              help="Re-describe all units ignoring source-hash cache.")
@click.option("--units-concurrency", type=int, default=1,
              help="Parallel `claude --print` workers for description "
                   "generation. 1=sequential (safest). 3-4 cuts wall time "
                   "but shares subscription rate-limit headroom. Max 4.")
def init(path: str, no_semantic: bool, yes: bool, force: bool,
         ollama_model: str | None, no_llm: bool,
         no_impact: bool, impact_only: bool, force_impact: bool,
         with_units: bool, force_units: bool, units_concurrency: int):
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
    Intent generation (per-function descriptions for better search):
      Default: uses Claude API (Haiku) if ANTHROPIC_API_KEY is set.
      --ollama gemma3:4b   Use local Ollama instead (must be installed).
      --no-llm             Skip intent generation entirely.
      Intents are only generated during init, not during impact_check,
      unless you explicitly set provider in .winkers/config.toml.

    \b
    Corporate SSL proxy? Two options:
      1. pip install pip-system-certs   (recommended, one-time fix)
      2. set WINKERS_SSL_VERIFY=0       (quick workaround, less secure)
    """
    root = Path(path).resolve()
    click.echo(f"Scanning {root} ...")

    if impact_only:
        _run_impact_only(root, force=force_impact)
        return

    builder = GraphBuilder()
    graph = builder.build(root)

    click.echo("Resolving cross-file calls ...")
    CrossFileResolver().resolve(graph, str(root))

    from winkers.value_locked import detect_value_locked
    detect_value_locked(graph, root)

    from winkers.class_attrs import detect_class_attrs
    detect_class_attrs(graph, root)

    # Path 2 of the literal-blind fix: AST expression-uses index. Built
    # against the value_locked tracked set produced just above. Cheap
    # — one AST pass per .py file. impact_check / diff_collections
    # consults this index when present (Wave 3 grep stays as fallback
    # for non-Python files).
    from winkers.expressions import ExpressionsStore, build_expressions_index
    expr_index = build_expressions_index(graph, root)
    if expr_index.values:
        ExpressionsStore(root).save(expr_index)
        click.echo(
            f"  Expression-uses index: {len(expr_index.values)} value(s) "
            f"with ≥3 occurrences."
        )

    _collect_git_history(root, graph)

    store = GraphStore(root)
    # Compute ast_hash for every function — used by `units` pipeline
    # staleness detection. Cheap (one read per file + AST normalization).
    # Without this, function_unit description-cache invalidation can't
    # work and `--with-units` re-describes nothing.
    all_files = list(graph.files.keys())
    store._compute_ast_hashes(graph, all_files)
    store.save(graph)
    _save_history_snapshot(root, graph)

    _update_gitignore(root)

    click.echo(
        f"Done. {len(graph.files)} files, {len(graph.functions)} functions, "
        f"{len(graph.call_edges)} call edges. ({graph.meta.get('parse_time_ms', 0):.0f} ms)"
    )

    _repair_sessions(root)
    _gc_runtime_sessions(root)
    _detect_and_lock_language(root)
    _run_debt_analysis(root, graph)

    if not no_semantic:
        _run_semantic_enrichment(root, graph, yes=yes, force=force)

    if not no_llm:
        impact_ran = False
        if not no_impact:
            impact_ran = _run_impact_generation(
                root, graph, force=force_impact,
            )
        if not impact_ran:
            _run_intent_generation(root, graph, ollama_model=ollama_model)
        # Persist intent/secondary_intents updates back to graph.json.
        store.save(graph)

    if with_units or force_units:
        concurrency = max(1, min(4, int(units_concurrency)))
        click.echo(f"\nUnits pipeline (concurrency={concurrency}):")
        try:
            us = _run_units_pipeline(
                root, graph,
                force=force_units, concurrency=concurrency,
            )
            click.echo(
                f"  fn: {us['fn_described']} described, {us['fn_failed']} failed; "
                f"templates: {us['template_described']} described, "
                f"{us['template_failed']} failed; "
                f"data: {us['data_described']} described, "
                f"{us['data_failed']} failed"
            )
            click.echo(
                f"  couplings: {us['couplings']} cluster(s); "
                f"embeddings: {us['embed_reused']} reused, "
                f"{us['embed_encoded']} encoded"
            )
        except Exception as exc:
            click.echo(f"  units pipeline failed: {exc}", err=True)

    _autodetect_ide(root)


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


@cli.command("cleanup-legacy")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Print what would be removed without deleting.",
)
def cleanup_legacy(path: str, dry_run: bool):
    """Remove legacy artifacts whose state was migrated to project.json/units.json.

    After Wave 4 of the 0.9.0 redesign these files are no longer the
    source of truth — they sit on disk as rollback safety nets:

    \b
      .winkers/semantic.json   -> .winkers/project.json::semantic
      .winkers/rules/rules.json -> .winkers/project.json::rules
      .winkers/impact.json     -> .winkers/units.json (per-unit)

    The cleanup is idempotent and skips files whose data hasn't been
    migrated yet (that would lose state). Run after a successful
    `winkers init` to reclaim disk space and ensure no consumer
    accidentally reads the stale legacy copy.

    `--dry-run` prints the plan without touching anything.
    """
    from winkers.descriptions.store import UnitsStore
    from winkers.project import PROJECT_FILE, ProjectStore
    from winkers.store import STORE_DIR

    root = Path(path).resolve()
    store_dir = root / STORE_DIR

    legacy_files: list[tuple[str, Path, str]] = []

    # semantic.json — migrated when project.json exists with non-default
    # semantic section. Conservative: require project.json present.
    sem = store_dir / "semantic.json"
    if sem.exists() and (store_dir / PROJECT_FILE).exists():
        bundle = ProjectStore(root).load()
        if bundle is not None:
            legacy_files.append((
                "semantic.json", sem,
                "folded into project.json (semantic section)",
            ))

    # rules/rules.json — same migration gate.
    rules = store_dir / "rules" / "rules.json"
    if rules.exists() and (store_dir / PROJECT_FILE).exists():
        legacy_files.append((
            "rules/rules.json", rules,
            "folded into project.json (rules section)",
        ))

    # impact.json — migrated when units.json carries function_unit
    # entries with `risk_level` (set by ImpactStore.save shim).
    imp = store_dir / "impact.json"
    if imp.exists():
        units = UnitsStore(root).load()
        has_impact = any(
            u.get("kind") == "function_unit" and u.get("risk_level")
            for u in units
        )
        if has_impact:
            legacy_files.append((
                "impact.json", imp,
                "folded into units.json (per-function risk fields)",
            ))

    if not legacy_files:
        click.echo("No legacy artifacts to clean up.")
        return

    total_bytes = sum(p.stat().st_size for _, p, _ in legacy_files)
    click.echo(f"Found {len(legacy_files)} legacy file(s), {total_bytes:,} bytes total:")
    for name, path_, reason in legacy_files:
        size = path_.stat().st_size
        click.echo(f"  {name}  ({size:,} bytes) — {reason}")

    if dry_run:
        click.echo("\n--dry-run: nothing removed.")
        return

    removed = 0
    for _, path_, _ in legacy_files:
        try:
            path_.unlink()
            removed += 1
        except OSError as e:
            click.echo(f"  WARN: cannot remove {path_}: {e}", err=True)

    click.echo(f"\n[ok] Removed {removed} legacy file(s).")


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


def _doctor_check_mcp_json(mcp_json: Path, root: Path, ok, warn) -> None:
    """Validate .mcp.json: parses, command resolves, args point at this root.

    Catches the failure mode from the 2026-04-26 invoicekit feedback (and
    confirmed in tespy on 2026-04-26): a project copied/migrated across
    machines carries a .mcp.json with a stale absolute path or a binary
    name that doesn't exist on the current host (e.g. `uvx winkers serve
    C:/Development/...` on a Linux VPS). MCP silently fails to start and
    the agent runs without architectural context.
    """
    import shutil

    try:
        cfg = json.loads(mcp_json.read_text(encoding="utf-8"))
    except Exception as e:
        warn(f".mcp.json is invalid JSON: {e}")
        return

    entry = (cfg.get("mcpServers") or {}).get("winkers")
    if not entry:
        warn(".mcp.json has no 'winkers' entry — run: winkers init")
        return

    cmd = entry.get("command") or ""
    args = entry.get("args") or []

    cmd_resolves = False
    if cmd:
        if Path(cmd).is_absolute() or "/" in cmd or "\\" in cmd:
            if Path(cmd).exists():
                ok(f".mcp.json command exists: {cmd}")
                cmd_resolves = True
            else:
                warn(
                    f".mcp.json command does not exist: {cmd}\n"
                    "      run: winkers init  (rewrites to current binary)"
                )
        else:
            resolved = shutil.which(cmd)
            if resolved:
                ok(f".mcp.json command on PATH: {cmd} → {resolved}")
                cmd_resolves = True
            else:
                warn(
                    f".mcp.json command not on PATH: {cmd}\n"
                    "      run: winkers init"
                )

    # Heuristic: any absolute-path arg that isn't a flag is the project
    # root. Compare to the doctor's `root` to catch stale paths from a
    # copied project (the bug Issue #3 in the invoicekit feedback).
    root_arg = next(
        (a for a in args
         if isinstance(a, str)
         and a not in ("serve",)
         and (Path(a).is_absolute() or "/" in a or "\\" in a)),
        None,
    )
    if root_arg:
        try:
            same = Path(root_arg).resolve() == root
        except Exception:
            same = False
        if same:
            ok(".mcp.json points at this project root")
        else:
            warn(
                f".mcp.json points at {root_arg} but current root is {root}\n"
                "      run: winkers init  (rewrites the path)"
            )
    elif cmd_resolves:
        # No root in args — that's fine for some configs but worth noting.
        ok(".mcp.json: no project root in args (server will use cwd)")


def _doctor_check_user_mcp(root: Path, ok, warn) -> None:
    """Check ~/.claude.json for user-scope winkers MCP.

    Project-scope MCP from `.mcp.json` requires a workspace trust dialog
    that Claude Code SKIPS in `--print` mode. Headless workflows (ticket
    runners, scheduled agents) therefore can't see project-scope MCP at
    all. The fix is a user-scope entry under top-level `mcpServers` in
    `~/.claude.json`. Without it, ticket-runner sessions on this project
    would run with no winkers architectural context — the failure mode
    we observed in tespy on 2026-04-25 (3 ticket sessions, 0 MCP calls).
    """
    user_cfg_path = Path.home() / ".claude.json"
    if not user_cfg_path.exists():
        warn(
            "~/.claude.json missing — Claude Code may not have run yet, "
            "or another IDE is in use; user-scope MCP not checked."
        )
        return

    try:
        user_cfg = json.loads(user_cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        warn(f"Could not read ~/.claude.json: {e}")
        return

    entry = (user_cfg.get("mcpServers") or {}).get("winkers")
    if not entry:
        warn(
            "No user-scope winkers MCP in ~/.claude.json.\n"
            "      claude --print (ticket runners, headless agents) skip the\n"
            "      project-scope trust dialog and will NOT see winkers MCP.\n"
            "      Fix: add to top-level mcpServers in ~/.claude.json:\n"
            f'        {{"winkers": {{"type": "stdio", "command": "<winkers bin>",\n'
            f'          "args": ["serve", "{root}"]}}}}'
        )
        return

    args = entry.get("args") or []
    root_arg = next(
        (a for a in args
         if isinstance(a, str)
         and a not in ("serve",)
         and (Path(a).is_absolute() or "/" in a or "\\" in a)),
        None,
    )
    if not root_arg:
        warn("User-scope winkers MCP has no project root in args.")
        return

    try:
        same = Path(root_arg).resolve() == root
    except Exception:
        same = False
    if same:
        ok("User-scope winkers MCP set to this project (~/.claude.json)")
    else:
        warn(
            f"User-scope winkers MCP points at {root_arg}, not this project.\n"
            f"      claude --print sessions for THIS project ({root}) won't see\n"
            "      winkers MCP. Edit ~/.claude.json mcpServers.winkers.args."
        )


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def doctor(path: str):
    """Check environment and project health."""
    import shutil
    import subprocess

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

    # MCP registration — health check
    mcp_json = root / ".mcp.json"
    if mcp_json.exists():
        _doctor_check_mcp_json(mcp_json, root, ok, warn)
    else:
        warn("No .mcp.json — run: winkers init (with .claude/ present)")

    # User-scope MCP — needed for `claude --print` headless workflows
    # (ticket runners) where the project-scope trust dialog is skipped.
    _doctor_check_user_mcp(root, ok, warn)

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

        if "<!-- winkers-start -->" in content:
            ok("CLAUDE.md Winkers pointer present")
        elif "<!-- winkers-semantic-start -->" in content:
            warn(
                "CLAUDE.md has legacy semantic block"
                " — run: winkers init (auto-migrates to pointer)"
            )
        else:
            warn("CLAUDE.md missing Winkers pointer — run: winkers init")
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


@cli.command("impact")
@click.argument("fn_query")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def impact_cmd(fn_query: str, path: str, as_json: bool):
    """Print pre-computed impact analysis for a function.

    \b
    Examples:
        winkers impact calculate_price
        winkers impact modules/pricing.py::calculate_price
        winkers impact build_graph --json
    """
    root = Path(path).resolve()
    graph = GraphStore(root).load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    from winkers.impact import ImpactStore

    impact = ImpactStore(root).load()
    if not impact.functions:
        click.echo(
            "No impact.json found. Run `winkers init` with an API provider "
            "(ANTHROPIC_API_KEY set) to generate it."
        )
        raise SystemExit(1)

    # Exact fn_id first, then by short name
    fn_id = fn_query if fn_query in graph.functions else None
    if fn_id is None:
        hits = [fid for fid in graph.functions if fid.endswith(f"::{fn_query}")]
        if len(hits) == 1:
            fn_id = hits[0]
        elif len(hits) > 1:
            click.echo(f"Ambiguous name '{fn_query}'. Candidates:")
            for h in hits:
                click.echo(f"  {h}")
            raise SystemExit(1)

    if fn_id is None:
        click.echo(f"Function not found: {fn_query}", err=True)
        raise SystemExit(1)

    report = impact.functions.get(fn_id)
    if report is None:
        click.echo(f"No impact report for {fn_id}. Re-run `winkers init`.")
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps({
            "function": fn_id,
            "report": report.model_dump(),
        }, indent=2))
        return

    click.echo(f"{fn_id}")
    click.echo(f"  risk:    {report.risk_level} ({report.risk_score:.2f})")
    click.echo(f"  summary: {report.summary}")
    if report.safe_operations:
        click.echo(f"  safe:        {', '.join(report.safe_operations)}")
    if report.dangerous_operations:
        click.echo(f"  dangerous:   {', '.join(report.dangerous_operations)}")
    if report.caller_classifications:
        click.echo("  callers:")
        for cc in report.caller_classifications:
            click.echo(
                f"    {cc.caller}  {cc.dependency_type}  "
                f"{cc.coupling}  {cc.update_effort}"
            )
            if cc.note:
                click.echo(f"      ↳ {cc.note}")
    if report.action_plan:
        click.echo(f"  action_plan: {report.action_plan}")


@cli.command("dupes")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--min-count", default=2, show_default=True,
              help="Minimum number of functions sharing a tag to report.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def dupes_cmd(path: str, min_count: int, as_json: bool):
    """Find duplicated inline logic via shared secondary_intents."""
    root = Path(path).resolve()
    graph = GraphStore(root).load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    groups: dict[str, list[str]] = {}
    for fn_id, fn in graph.functions.items():
        for tag in fn.secondary_intents or []:
            groups.setdefault(tag, []).append(fn_id)

    filtered = {
        tag: sorted(fns) for tag, fns in groups.items() if len(fns) >= min_count
    }
    filtered = dict(sorted(filtered.items(), key=lambda kv: -len(kv[1])))

    if as_json:
        click.echo(json.dumps({"groups": filtered}, indent=2))
        return

    if not filtered:
        click.echo(
            f"No duplicated logic found (threshold: {min_count}). "
            "Run `winkers init` with an API provider to populate secondary_intents."
        )
        return

    for tag, fn_ids in filtered.items():
        click.echo(f'"{tag}" ({len(fn_ids)} functions):')
        for fid in fn_ids:
            fn = graph.functions.get(fid)
            if fn is None:
                continue
            click.echo(f"  {fn.name}    {fn.file}:{fn.line_start}")
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


# ---------------------------------------------------------------------------
# describe-fn — Phase 1 description-author CLI
# ---------------------------------------------------------------------------

@cli.command("describe-fn")
@click.argument("fn_id")
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root containing .winkers/graph.json")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the formatted prompt without invoking claude")
@click.option("--save", is_flag=True, default=False,
              help="Append/replace this unit in .winkers/units.json")
def describe_fn(fn_id: str, root: str, dry_run: bool, save: bool):
    """Generate a rich description for one function unit.

    Reads .winkers/graph.json to find the function and its 1-2 nearest
    callers, formats the description-author prompt, and either prints
    the prompt (--dry-run) or invokes `claude --print` to produce the
    description (subscription-first, no API key required).

    \b
    Example:
      winkers describe-fn engine/chp_model.py::solve_design -r /path/to/project
    """
    import json as _json

    from winkers.descriptions.author import author_function_description
    from winkers.descriptions.prompts import format_function_prompt

    root_path = Path(root).resolve()
    graph_path = root_path / ".winkers" / "graph.json"
    if not graph_path.exists():
        click.echo(
            f"No graph at {graph_path}. Run `winkers init` first.",
            err=True,
        )
        raise SystemExit(2)

    graph = _json.loads(graph_path.read_text(encoding="utf-8"))
    fn = graph["functions"].get(fn_id)
    if fn is None:
        click.echo(f"Function not found: {fn_id}", err=True)
        # Help the user discover correct ids.
        suffix = fn_id.split("::")[-1] if "::" in fn_id else fn_id
        candidates = [k for k in graph["functions"] if suffix in k]
        if candidates:
            click.echo("Similar fn_ids:", err=True)
            for c in candidates[:5]:
                click.echo(f"  {c}", err=True)
        raise SystemExit(2)

    # Source slice from line range — graph.json holds the boundaries already.
    src_path = root_path / fn["file"]
    if not src_path.exists():
        click.echo(f"Source file missing: {src_path}", err=True)
        raise SystemExit(2)
    src_lines = src_path.read_text(encoding="utf-8").splitlines()
    fn_source = "\n".join(src_lines[fn["line_start"] - 1: fn["line_end"]])

    # Top 2 callers, signatures only — see prompts.py docstring for why
    # bodies are deliberately excluded (cache-invalidation scope).
    caller_ids: list[str] = []
    for edge in graph.get("call_edges", []):
        if edge["target_fn"] == fn_id and edge["source_fn"] not in caller_ids:
            caller_ids.append(edge["source_fn"])
            if len(caller_ids) >= 2:
                break
    caller_sigs: list[str] = []
    for cid in caller_ids:
        c = graph["functions"].get(cid)
        if not c:
            continue
        params = ", ".join(p["name"] for p in c.get("params", []))
        prefix = f"{c['class_name']}." if c.get("class_name") else ""
        caller_sigs.append(f"def {prefix}{c['name']}({params})")

    # Display name in prompt — qualified for methods.
    display_name = (
        f"{fn['class_name']}.{fn['name']}"
        if fn.get("class_name") else fn["name"]
    )

    if dry_run:
        prompt = format_function_prompt(
            fn_source, fn["file"], display_name, callers=caller_sigs,
        )
        click.echo(prompt)
        return

    click.echo(f"Generating description for {fn_id}...", err=True)
    desc = author_function_description(
        fn_source=fn_source,
        file_path=fn["file"],
        fn_name=display_name,
        callers=caller_sigs,
        cwd=root_path,
    )
    if desc is None:
        click.echo("Description generation failed — see logs above.", err=True)
        raise SystemExit(1)

    unit = {
        "id": fn_id,
        "kind": "function_unit",
        "name": display_name,
        "anchor": {
            "file": fn["file"],
            "fn": fn["name"],
            **({"class": fn["class_name"]} if fn.get("class_name") else {}),
        },
        "source_hash": fn.get("ast_hash"),
        "description": desc.description,
        "hardcoded_artifacts": [a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts],
    }
    click.echo(_json.dumps(unit, ensure_ascii=False, indent=2))

    if save:
        # Lightweight merge until units_store.py (Phase 1.7) lands.
        units_path = root_path / ".winkers" / "units.json"
        existing = {"units": []}
        if units_path.exists():
            try:
                existing = _json.loads(units_path.read_text(encoding="utf-8"))
                existing.setdefault("units", [])
            except Exception:
                pass
        existing["units"] = [u for u in existing["units"]
                             if u.get("id") != fn_id]
        existing["units"].append(unit)
        units_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"Saved to {units_path}", err=True)


# ---------------------------------------------------------------------------
# describe-section — Phase 1 description-author CLI for template sections
# ---------------------------------------------------------------------------

@cli.command("describe-section")
@click.argument("section_ref")
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print formatted prompt without invoking claude")
@click.option("--save", is_flag=True, default=False,
              help="Append/replace this unit in .winkers/units.json")
def describe_section(section_ref: str, root: str, dry_run: bool, save: bool):
    """Generate a description for one template section.

    SECTION_REF format: "<template-path>#<section-id>"

    \b
    Example:
      winkers describe-section templates/index.html#calc-sub-approach \\
                              --root /path/to/project
    """
    import json as _json

    from winkers.descriptions.author import author_template_description
    from winkers.descriptions.prompts import format_template_section_prompt
    from winkers.templates.scanner import filter_leaves, scan_template

    if "#" not in section_ref:
        click.echo(
            "Section ref must be '<template-path>#<section-id>' "
            "(e.g. 'templates/index.html#calc-sub-approach')",
            err=True,
        )
        raise SystemExit(2)

    template_rel, section_id = section_ref.split("#", 1)
    root_path = Path(root).resolve()
    template_path = root_path / template_rel
    if not template_path.exists():
        click.echo(f"Template not found: {template_path}", err=True)
        raise SystemExit(2)

    sections = scan_template(template_path)
    sec = next((s for s in sections if s.id == section_id), None)
    if sec is None:
        click.echo(f"Section #{section_id} not found in {template_rel}", err=True)
        ids = sorted(s.id for s in filter_leaves(sections))
        if ids:
            click.echo("Available leaf section ids:", err=True)
            for i in ids:
                click.echo(f"  {i}", err=True)
        raise SystemExit(2)

    # Neighbor ids — gives the LLM orientation; keeps the prompt cheap by
    # not including their content.
    neighbors = [s.id for s in filter_leaves(sections) if s.id != section_id][:5]

    if dry_run:
        prompt = format_template_section_prompt(
            section_html=sec.content,
            file_path=template_rel,
            section_id=section_id,
            leading_comment=sec.leading_comment,
            neighbor_section_ids=neighbors,
        )
        click.echo(prompt)
        return

    click.echo(f"Generating description for {section_ref}...", err=True)
    desc = author_template_description(
        section_html=sec.content,
        file_path=template_rel,
        section_id=section_id,
        leading_comment=sec.leading_comment,
        neighbor_section_ids=neighbors,
        cwd=root_path,
    )
    if desc is None:
        click.echo("Description generation failed — see logs above.", err=True)
        raise SystemExit(1)

    unit_id = f"template:{template_rel}#{section_id}"
    unit = {
        "id": unit_id,
        "kind": "traceability_unit",
        "name": f"Section #{section_id} ({template_rel})",
        "source_files": [template_rel],
        "source_anchors": [f"{template_rel}#{section_id}"],
        "description": desc.description,
        "hardcoded_artifacts": [a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts],
    }
    click.echo(_json.dumps(unit, ensure_ascii=False, indent=2))

    if save:
        units_path = root_path / ".winkers" / "units.json"
        existing = {"units": []}
        if units_path.exists():
            try:
                existing = _json.loads(units_path.read_text(encoding="utf-8"))
                existing.setdefault("units", [])
            except Exception:
                pass
        existing["units"] = [u for u in existing["units"]
                             if u.get("id") != unit_id]
        existing["units"].append(unit)
        units_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"Saved to {units_path}", err=True)


# ---------------------------------------------------------------------------
# describe-data — Phase 1.10 description-author CLI for data files
# ---------------------------------------------------------------------------

@cli.command("describe-data")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root (file path is relative to this)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print formatted prompt without invoking claude")
@click.option("--save", is_flag=True, default=False,
              help="Append/replace this unit in .winkers/units.json")
def describe_data(file_path: str, root: str, dry_run: bool, save: bool):
    """Generate a description for one data file (JSON / YAML / TOML).

    \b
    Example:
      winkers describe-data data/tespy_topology.json -r /path/to/project
    """
    import json as _json

    from winkers.data_files.scanner import read_data_file
    from winkers.descriptions.author import author_data_file_description
    from winkers.descriptions.prompts import format_data_file_prompt
    from winkers.descriptions.store import data_file_hash

    root_path = Path(root).resolve()
    fp = Path(file_path).resolve()
    entry = read_data_file(fp, root_path)
    if entry is None:
        click.echo(
            f"Cannot read or file too large: {fp} "
            f"(size cap defined in winkers.data_files.scanner.MAX_FILE_BYTES)",
            err=True,
        )
        raise SystemExit(2)

    if dry_run:
        click.echo(format_data_file_prompt(entry.content, entry.rel_path))
        return

    click.echo(
        f"Generating description for data:{entry.rel_path}...", err=True,
    )
    desc = author_data_file_description(
        file_content=entry.content,
        file_path=entry.rel_path,
        cwd=root_path,
    )
    if desc is None:
        click.echo("Description generation failed — see logs above.", err=True)
        raise SystemExit(1)

    unit_id = f"data:{entry.rel_path}"
    unit = {
        "id": unit_id,
        "kind": "traceability_unit",
        "name": f"Data file {entry.rel_path}",
        "source_files": [entry.rel_path],
        "source_anchors": [entry.rel_path],
        "source_hash": data_file_hash(entry.content),
        "description": desc.description,
        "hardcoded_artifacts": [
            a.model_dump(exclude_none=True)
            for a in desc.hardcoded_artifacts
        ],
    }
    click.echo(_json.dumps(unit, ensure_ascii=False, indent=2))

    if save:
        units_path = root_path / ".winkers" / "units.json"
        existing = {"units": []}
        if units_path.exists():
            try:
                existing = _json.loads(units_path.read_text(encoding="utf-8"))
                existing.setdefault("units", [])
            except Exception:
                pass
        existing["units"] = [u for u in existing["units"]
                             if u.get("id") != unit_id]
        existing["units"].append(unit)
        units_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"Saved to {units_path}", err=True)


# ---------------------------------------------------------------------------
# couplings — detect cross-file couplings from hardcoded_artifacts
# ---------------------------------------------------------------------------

@cli.command("couplings")
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root containing .winkers/units.json")
@click.option("--save", is_flag=True, default=False,
              help="Append detected couplings to .winkers/units.json as "
                   "traceability_units (replaces existing auto-detected ones)")
@click.option("--min-files", type=int, default=2,
              help="Minimum distinct files for a cluster to qualify")
@click.option("--min-hits", type=int, default=2,
              help="Minimum total hits for a cluster")
@click.option("--limit", type=int, default=30,
              help="Maximum clusters to display")
def couplings(root: str, save: bool, min_files: int, min_hits: int,
              limit: int):
    """Detect cross-file couplings from hardcoded_artifacts.

    Reads .winkers/units.json (populated by `winkers describe-fn` /
    `describe-section`), inverts the artifact lists into a value→units
    map, and emits clusters where the same canonical value appears in
    multiple files. Each cluster becomes a proposed traceability_unit.

    \b
    Workflow:
      winkers describe-fn <fn_id> --save        # populate function units
      winkers describe-section <ref> --save     # populate template units
      winkers couplings                          # inspect couplings
      winkers couplings --save                   # commit to units.json
    """
    import json as _json

    from winkers.descriptions.aggregator import (
        detect_couplings,
        proposed_to_unit,
    )

    root_path = Path(root).resolve()
    units_path = root_path / ".winkers" / "units.json"
    if not units_path.exists():
        click.echo(
            f"No units file at {units_path}. Run `winkers describe-fn` / "
            f"`winkers describe-section --save` first.",
            err=True,
        )
        raise SystemExit(2)

    data = _json.loads(units_path.read_text(encoding="utf-8"))
    units = data.get("units", [])
    # Detection runs only on units with artifacts; auto-detected coupling
    # units (origin=auto-detected) are excluded so we don't bootstrap
    # couplings from prior couplings.
    candidate = [
        u for u in units
        if u.get("kind") in ("function_unit", "traceability_unit")
        and u.get("hardcoded_artifacts")
        and (u.get("meta") or {}).get("origin") != "auto-detected"
    ]

    click.echo(
        f"Scanning {len(candidate)} units with hardcoded_artifacts "
        f"(of {len(units)} total)...",
        err=True,
    )

    clusters = detect_couplings(
        candidate, min_hits=min_hits, min_files=min_files,
    )

    if not clusters:
        click.echo("No cross-file couplings detected.")
        return

    click.echo(f"\n{len(clusters)} coupling cluster(s) found:\n")
    for c in clusters[:limit]:
        display = c.canonical_value
        if len(display) > 50:
            display = display[:47] + "..."
        click.echo(
            f"  [{c.primary_kind}]  {display!r}  "
            f"— {c.file_count} files, {c.hit_count} hits, "
            f"uniformity={c.kind_uniformity:.2f}"
        )
        for h in c.hits[:4]:
            ctx = h.artifact.context[:60]
            click.echo(f"      • {h.file:35s}  {h.unit_id:60s}  {ctx}")
        if len(c.hits) > 4:
            click.echo(f"      • ... and {len(c.hits) - 4} more")
        click.echo()

    if len(clusters) > limit:
        click.echo(f"  ... {len(clusters) - limit} more cluster(s) "
                   f"omitted (use --limit to show more)")

    if save:
        # Strip prior auto-detected couplings; replace with current set.
        kept = [u for u in units
                if (u.get("meta") or {}).get("origin") != "auto-detected"]
        proposed_units = [proposed_to_unit(c) for c in clusters]
        new_data = {"units": kept + proposed_units}
        units_path.write_text(
            _json.dumps(new_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(
            f"\nSaved {len(proposed_units)} traceability_units to "
            f"{units_path} (replaced {len(units) - len(kept)} prior "
            f"auto-detected entries)",
            err=True,
        )




