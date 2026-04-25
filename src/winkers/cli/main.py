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


def _run_units_pipeline(root: Path, graph, force: bool = False,
                        concurrency: int = 1) -> dict:
    """Phase 1 description-first units pipeline.

    1. Scan templates for sections (winkers/templates/scanner.py).
    2. Identify stale function_units (ast_hash mismatch) and stale
       template sections (content_hash mismatch).
    3. Re-describe stale units via `claude --print` subprocess
       (subscription auth, sequential — concurrency risks rate limits).
    4. Prune orphan units (graph fn no longer exists, section disappeared).
    5. Run coupling aggregator over all units → traceability_units.
    6. Re-embed only changed units (BGE-M3, incremental hash check).

    Returns a stats dict for the caller's progress report.
    """
    from winkers.data_files.scanner import (
        discover_data_files,
        read_data_file,
    )
    from winkers.descriptions.aggregator import (
        detect_couplings,
        proposed_to_unit,
    )
    from winkers.descriptions.author import (
        author_data_file_description,
        author_function_description,
        author_template_description,
    )
    from winkers.descriptions.store import (
        UnitsStore,
        data_file_hash,
        section_hash,
    )
    from winkers.embeddings import (
        INDEX_FILENAME,
        embed_units,
        load_index,
        save_index,
    )
    from winkers.templates.scanner import scan_project

    store = UnitsStore(root)
    units = store.load()
    stats = {
        "fn_described": 0, "fn_failed": 0,
        "template_described": 0, "template_failed": 0,
        "data_described": 0, "data_failed": 0,
        "couplings": 0,
        "embed_reused": 0, "embed_encoded": 0,
    }

    # ── 1. Scan templates ────────────────────────────────────────────────
    sections = scan_project(root)  # filtered to leaves by scanner
    live_template_ids = {f"template:{s.file}#{s.id}" for s in sections}
    section_by_uid = {f"template:{s.file}#{s.id}": s for s in sections}

    # ── 1b. Scan data files (JSON/YAML) ─────────────────────────────────
    data_paths = discover_data_files(root)
    data_entries: list = []
    for p in data_paths:
        e = read_data_file(p, root)
        if e is not None:
            data_entries.append(e)
    live_data_ids = {f"data:{e.rel_path}" for e in data_entries}
    data_by_uid = {f"data:{e.rel_path}": e for e in data_entries}

    # ── 2. Identify stale ────────────────────────────────────────────────
    graph_fn_summary = {
        fn.id: {"ast_hash": fn.ast_hash}
        for fn in graph.functions.values()
    }
    live_fn_ids = set(graph_fn_summary.keys())
    if force:
        stale_fn_ids = live_fn_ids
        stale_tpl_ids = live_template_ids
        stale_data_ids = live_data_ids
    else:
        stale_fn_ids = store.stale_function_units(units, graph_fn_summary)
        stale_tpl_ids = store.stale_template_units(units, sections)
        stale_data_ids = store.stale_data_file_units(units, data_entries)

    click.echo(
        f"  {len(stale_fn_ids)} function unit(s), "
        f"{len(stale_tpl_ids)} template section(s), "
        f"{len(stale_data_ids)} data file(s) need description"
    )

    # ── 3. Author descriptions for stale function units ────────────────
    if stale_fn_ids:
        # Build per-fn contexts up front (sync, cheap) so the parallel
        # phase only does the slow work (`claude --print`).
        fn_contexts: list[dict] = []
        for fn_id in sorted(stale_fn_ids):
            fn = graph.functions.get(fn_id)
            if fn is None:
                continue
            src_path = root / fn.file
            if not src_path.exists():
                stats["fn_failed"] += 1
                continue
            src_lines = src_path.read_text(encoding="utf-8").splitlines()
            fn_source = "\n".join(
                src_lines[fn.line_start - 1: fn.line_end]
            )
            caller_sigs: list[str] = []
            seen: set[str] = set()
            for edge in graph.call_edges:
                if edge.target_fn != fn_id or edge.source_fn in seen:
                    continue
                seen.add(edge.source_fn)
                c = graph.functions.get(edge.source_fn)
                if c is None:
                    continue
                params = ", ".join(p.name for p in c.params)
                prefix = f"{c.class_name}." if c.class_name else ""
                caller_sigs.append(f"def {prefix}{c.name}({params})")
                if len(caller_sigs) >= 2:
                    break
            display_name = (
                f"{fn.class_name}.{fn.name}" if fn.class_name else fn.name
            )
            fn_contexts.append({
                "fn_id": fn_id, "fn": fn, "fn_source": fn_source,
                "display_name": display_name, "caller_sigs": caller_sigs,
            })

        # Run `claude --print` calls in a thread pool. Each subprocess
        # blocks on its own stdin/stdout, so threads are fine (no GIL
        # contention on subprocess.run). Concurrency caps shared
        # subscription rate-limit pressure — recommended ≤4.
        def _describe_fn(ctx):
            return ctx, author_function_description(
                fn_source=ctx["fn_source"],
                file_path=ctx["fn"].file,
                fn_name=ctx["display_name"],
                callers=ctx["caller_sigs"],
                cwd=root,
            )

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with click.progressbar(
            length=len(fn_contexts), label="Function descriptions",
        ) as bar:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = [ex.submit(_describe_fn, c) for c in fn_contexts]
                for fut in as_completed(futures):
                    ctx, desc = fut.result()
                    if desc is None:
                        stats["fn_failed"] += 1
                    else:
                        fn = ctx["fn"]
                        unit = {
                            "id": ctx["fn_id"],
                            "kind": "function_unit",
                            "name": ctx["display_name"],
                            "anchor": {
                                "file": fn.file,
                                "fn": fn.name,
                                **({"class": fn.class_name}
                                   if fn.class_name else {}),
                            },
                            "source_hash": fn.ast_hash,
                            "description": desc.description,
                            "hardcoded_artifacts": [
                                a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts
                            ],
                        }
                        # Upsert is single-threaded — only the main thread
                        # runs as_completed callbacks, no race on `units`.
                        units = store.upsert(units, unit)
                        # Persist after each unit so an interrupted run
                        # leaves a usable partial index (resumable on next
                        # `init --with-units`).
                        store.save(units)
                        stats["fn_described"] += 1
                    bar.update(1)

    # ── 4. Author descriptions for stale template sections ─────────────
    if stale_tpl_ids:
        tpl_contexts: list[dict] = []
        for uid in sorted(stale_tpl_ids):
            sec = section_by_uid.get(uid)
            if sec is None:
                continue
            neighbors = [s.id for s in sections if s.id != sec.id][:5]
            tpl_contexts.append({"uid": uid, "sec": sec, "neighbors": neighbors})

        def _describe_tpl(ctx):
            sec = ctx["sec"]
            return ctx, author_template_description(
                section_html=sec.content,
                file_path=sec.file,
                section_id=sec.id,
                leading_comment=sec.leading_comment,
                neighbor_section_ids=ctx["neighbors"],
                cwd=root,
            )

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with click.progressbar(
            length=len(tpl_contexts), label="Template descriptions",
        ) as bar:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = [ex.submit(_describe_tpl, c) for c in tpl_contexts]
                for fut in as_completed(futures):
                    ctx, desc = fut.result()
                    if desc is None:
                        stats["template_failed"] += 1
                    else:
                        sec = ctx["sec"]
                        unit = {
                            "id": ctx["uid"],
                            "kind": "traceability_unit",
                            "name": f"Section #{sec.id} ({sec.file})",
                            "source_files": [sec.file],
                            "source_anchors": [f"{sec.file}#{sec.id}"],
                            "source_hash": section_hash(sec.content),
                            "description": desc.description,
                            "hardcoded_artifacts": [
                                a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts
                            ],
                        }
                        units = store.upsert(units, unit)
                        store.save(units)
                        stats["template_described"] += 1
                    bar.update(1)

    # ── 4b. Author descriptions for stale data files ────────────────────
    if stale_data_ids:
        data_contexts: list[dict] = []
        for uid in sorted(stale_data_ids):
            entry = data_by_uid.get(uid)
            if entry is None:
                continue
            data_contexts.append({"uid": uid, "entry": entry})

        def _describe_data(ctx):
            entry = ctx["entry"]
            return ctx, author_data_file_description(
                file_content=entry.content,
                file_path=entry.rel_path,
                cwd=root,
            )

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with click.progressbar(
            length=len(data_contexts), label="Data file descriptions",
        ) as bar:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = [ex.submit(_describe_data, c) for c in data_contexts]
                for fut in as_completed(futures):
                    ctx, desc = fut.result()
                    if desc is None:
                        stats["data_failed"] += 1
                    else:
                        entry = ctx["entry"]
                        unit = {
                            "id": ctx["uid"],
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
                        units = store.upsert(units, unit)
                        store.save(units)
                        stats["data_described"] += 1
                    bar.update(1)

    # ── 5. Prune orphans ────────────────────────────────────────────────
    units = store.prune_orphans(
        units, live_fn_ids, live_template_ids, live_data_ids,
    )

    # ── 6. Coupling aggregator ──────────────────────────────────────────
    # Re-detect from primary units (exclude prior auto-detected couplings
    # so we don't bootstrap couplings from couplings).
    primary = [
        u for u in units
        if u.get("hardcoded_artifacts")
        and (u.get("meta") or {}).get("origin") != "auto-detected"
    ]
    clusters = detect_couplings(primary)
    units = [
        u for u in units
        if (u.get("meta") or {}).get("origin") != "auto-detected"
    ]
    units.extend(proposed_to_unit(c) for c in clusters)
    stats["couplings"] = len(clusters)

    # ── 7. Save units.json ──────────────────────────────────────────────
    store.save(units)

    # ── 8. Embeddings (incremental) ─────────────────────────────────────
    idx_path = root / ".winkers" / INDEX_FILENAME
    existing_idx = load_index(idx_path)
    new_idx, embed_stats = embed_units(units, existing=existing_idx, force=force)
    save_index(new_idx, idx_path)
    stats["embed_reused"] = embed_stats["reused"]
    stats["embed_encoded"] = embed_stats["encoded"]

    return stats


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
    _install_winkers_pointer(root)


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


# Old semantic-summary markers — kept only for the one-shot migration that
# rewrites them into the new pointer block. New installs use _WINKERS_*.
_OLD_SEM_START = "<!-- winkers-semantic-start -->"
_OLD_SEM_END = "<!-- winkers-semantic-end -->"

_WINKERS_START = "<!-- winkers-start -->"
_WINKERS_END = "<!-- winkers-end -->"

_POINTER_BLOCK = (
    f"{_WINKERS_START}\n"
    "### Winkers\n\n"
    "This project uses Winkers (function-level dependency graph + semantic layer"
    " + coding rules). Before non-trivial edits call"
    " `orient(include=[\"map\",\"conventions\",\"rules_list\"])`"
    " for zones, data flow, domain context, and rules.\n"
    f"{_WINKERS_END}"
)


def _install_winkers_pointer(root: Path) -> None:
    """Ensure CLAUDE.md has a single static Winkers pointer block.

    The pointer is a short pointer to `orient` — it does NOT duplicate
    semantic.json content. Orient is the single source of truth for
    data_flow / domain_context / zones / rules, so there is nothing to drift.

    Migration: older CLAUDE.md files may contain the deprecated
    `<!-- winkers-semantic-start -->` block with verbatim semantic data;
    we replace it in-place on the next run.
    """
    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        return

    existing = claude_md.read_text(encoding="utf-8")

    # One-shot migration of the old semantic-summary block.
    if _OLD_SEM_START in existing and _OLD_SEM_END in existing:
        s = existing.index(_OLD_SEM_START)
        e = existing.index(_OLD_SEM_END) + len(_OLD_SEM_END)
        updated = existing[:s] + _POINTER_BLOCK + existing[e:]
        claude_md.write_text(updated, encoding="utf-8")
        click.echo("  [ok] Migrated CLAUDE.md semantic block → Winkers pointer")
        return

    if _WINKERS_START in existing and _WINKERS_END in existing:
        s = existing.index(_WINKERS_START)
        e = existing.index(_WINKERS_END) + len(_WINKERS_END)
        # Idempotent — if block is already up to date, skip the write.
        if existing[s:e] == _POINTER_BLOCK:
            return
        updated = existing[:s] + _POINTER_BLOCK + existing[e:]
        claude_md.write_text(updated, encoding="utf-8")
        click.echo("  [ok] Refreshed CLAUDE.md Winkers pointer")
        return

    claude_md.write_text(
        existing.rstrip() + "\n\n" + _POINTER_BLOCK + "\n", encoding="utf-8"
    )
    click.echo("  [ok] Added Winkers pointer to CLAUDE.md")


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
    changed = _upsert_hook(
        session_end, AUTO_COMMIT_MARKER, autocommit_cmd,
        timeout=30, label="SessionEnd autocommit",
    ) or changed

    # --- record hook ---
    record_cmd = f"{hook_bin} record --hook"
    changed = _upsert_hook(
        session_end, "record", record_cmd,
        timeout=60, label="SessionEnd record",
    ) or changed

    # --- Tool permissions ---
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])

    if WINKERS_TOOLS_PERMISSION not in allow:
        allow.append(WINKERS_TOOLS_PERMISSION)
        click.echo(f"  [ok] Tool permissions added: {WINKERS_TOOLS_PERMISSION}")
        changed = True
    else:
        click.echo("  [ok] Tool permissions already set.")

    # --- Interactive hooks (before_create, duplicate gate, impact_check) ---
    changed = _install_interactive_hooks(hooks, hook_bin, root) or changed

    if changed:
        settings_path.write_text(
            json.dumps(settings, indent=2), encoding="utf-8",
        )


def _upsert_hook(
    hook_list: list[dict], marker: str, command: str,
    timeout: int = 10, label: str = "",
) -> bool:
    """Insert or update a hook entry. Returns True if changed."""
    for entry in hook_list:
        for h in entry.get("hooks", []):
            if marker in h.get("command", ""):
                if h["command"] == command:
                    click.echo(f"  [ok] {label} hook up to date.")
                    return False
                # Path changed — update in place
                h["command"] = command
                click.echo(f"  [ok] {label} hook path updated.")
                return True

    # Not found — add new
    hook_list.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    })
    click.echo(f"  [ok] {label} hook installed.")
    return True


def _install_interactive_hooks(hooks: dict, hook_bin: str, root: Path) -> bool:
    """Install UserPromptSubmit, PreToolUse, PostToolUse, Stop hooks."""
    changed = False
    root_posix = str(root).replace("\\", "/")

    hook_defs = [
        {
            "event": "UserPromptSubmit",
            "marker": "prompt-enrich",
            "command": f"{hook_bin} hook prompt-enrich {root_posix}",
            "timeout": 2,
            "matcher": "",
            "label": "prompt-enrich",
        },
        {
            "event": "PreToolUse",
            "marker": "pre-write",
            "command": f"{hook_bin} hook pre-write {root_posix}",
            "timeout": 3,
            "matcher": "Write|Edit|MultiEdit",
            "label": "pre-write duplicate gate",
        },
        {
            "event": "PostToolUse",
            "marker": "post-write",
            "command": f"{hook_bin} hook post-write {root_posix}",
            "timeout": 5,
            "matcher": "Write|Edit|MultiEdit",
            "label": "post-write impact check",
        },
        # Phase 3 — coherence audit lifecycle.
        # SessionStart records the git HEAD baseline so SessionEnd's
        # audit can compute an honest diff (not just HEAD~1 vs HEAD).
        {
            "event": "SessionStart",
            "marker": "session-start",
            "command": f"{hook_bin} hook session-start {root_posix}",
            "timeout": 2,
            "matcher": "",
            "label": "session-start baseline",
        },
        # SessionEnd uses the *spawn* variant — it detaches the slow
        # `claude --print` audit subprocess and returns immediately so
        # the session-end completion isn't blocked. The actual audit
        # runs out-of-band and writes `.winkers_pending.md`.
        {
            "event": "SessionEnd",
            "marker": "stop-audit-spawn",
            "command": f"{hook_bin} hook stop-audit-spawn {root_posix}",
            "timeout": 5,
            "matcher": "",
            "label": "stop-audit (detached)",
        },
    ]

    # 0.8.1: session_done muted — remove legacy Stop/session-audit hook if present.
    stop_hooks = hooks.get("Stop", [])
    filtered_stop: list = []
    for entry in stop_hooks:
        kept = [h for h in entry.get("hooks", []) if "session-audit" not in h.get("command", "")]
        if kept:
            entry["hooks"] = kept
            filtered_stop.append(entry)
        elif entry.get("hooks"):
            changed = True
            click.echo("  [ok] Stop session-audit hook removed (session_done muted in 0.8.1).")
    if "Stop" in hooks:
        if filtered_stop:
            hooks["Stop"] = filtered_stop
        else:
            del hooks["Stop"]

    for hdef in hook_defs:
        event_hooks = hooks.setdefault(hdef["event"], [])
        label = f"{hdef['event']} {hdef['label']}"

        # Check if exists and path is current
        found = False
        for entry in event_hooks:
            for h in entry.get("hooks", []):
                if hdef["marker"] in h.get("command", ""):
                    found = True
                    if h["command"] != hdef["command"]:
                        h["command"] = hdef["command"]
                        click.echo(f"  [ok] {label} hook path updated.")
                        changed = True
                    else:
                        click.echo(f"  [ok] {label} hook up to date.")
                    break
            if found:
                break

        if not found:
            entry_new: dict = {
                "hooks": [{
                    "type": "command",
                    "command": hdef["command"],
                    "timeout": hdef["timeout"],
                }],
            }
            if hdef["matcher"]:
                entry_new["matcher"] = hdef["matcher"]
            event_hooks.append(entry_new)
            click.echo(f"  [ok] {label} hook installed.")
            changed = True

    return changed


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


# ---------------------------------------------------------------------------
# hook subcommands — Claude Code hooks protocol (stdin JSON → stdout JSON)
# ---------------------------------------------------------------------------

@cli.group()
def hook():
    """Claude Code hook handlers (called by hooks, not directly)."""


@hook.command("prompt-enrich")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_prompt_enrich(path: str):
    """UserPromptSubmit hook: detect creation intent, inject before_create."""
    from winkers.hooks.prompt_enrich import run
    run(Path(path).resolve())


@hook.command("pre-write")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_pre_write(path: str):
    """PreToolUse hook: AST hash duplicate gate for Write/Edit."""
    from winkers.hooks.pre_write import run
    run(Path(path).resolve())


@hook.command("post-write")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_post_write(path: str):
    """PostToolUse hook: impact check on file writes (graph update + impact + coherence)."""
    from winkers.hooks.post_write import run
    run(Path(path).resolve())


@hook.command("session-audit")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_session_audit(path: str):
    """Stop hook: session audit gate (muted in 0.8.1 — left for legacy settings.json)."""
    from winkers.hooks.session_audit import run
    run(Path(path).resolve())


@hook.command("session-start")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_session_start(path: str):
    """SessionStart hook: persist current git HEAD as audit baseline.

    Called at the very beginning of each Claude Code session. The
    SessionEnd `stop-audit` reads this file to compute an honest
    diff between session-start and session-end (not just last commit).
    """
    from winkers.hooks.session_start import run
    run(Path(path).resolve())


@hook.command("stop-audit")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_stop_audit(path: str):
    """SessionEnd hook: run cross-file coherence audit (in-process).

    Synchronous — takes ~30-60s to spawn `claude --print`. Use
    `stop-audit-spawn` from settings.json so SessionEnd doesn't block
    on this. Direct invocation is for manual runs / tests.
    """
    from winkers.hooks.stop_audit import run
    run(Path(path).resolve())


@hook.command("stop-audit-spawn")
@click.argument("path", default=".", type=click.Path(exists=True))
def hook_stop_audit_spawn(path: str):
    """SessionEnd hook entry: detach `stop-audit` and return immediately.

    Wired into `.claude/settings.json` SessionEnd. Spawns the
    `winkers hook stop-audit` child as a detached process so the
    Claude Code session-end completion isn't blocked on the ~30s
    audit subprocess.

    Cross-platform: uses `subprocess.Popen` without `wait()`, with
    flags that prevent the child from being killed when the parent
    SessionEnd hook exits.
    """
    import subprocess
    import sys

    root = Path(path).resolve()
    # Path to our own CLI — same one currently executing.
    winkers_bin = sys.executable
    if winkers_bin.lower().endswith("python.exe") or \
       winkers_bin.lower().endswith("python"):
        # Running via `python -m`; spawn the same way.
        cmd = [winkers_bin, "-m", "winkers.cli.main",
               "hook", "stop-audit", str(root)]
    else:
        # Likely the installed `winkers.exe` entry point.
        cmd = [winkers_bin, "hook", "stop-audit", str(root)]

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(root),
    }
    if sys.platform == "win32":
        # Detach so the child outlives the parent SessionEnd hook.
        # CREATE_NO_WINDOW (0x08000000) suppresses cmd-window pop-up;
        # DETACHED_PROCESS (0x00000008) breaks the parent-child link.
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
        )
    else:
        # POSIX: start a new session so SIGHUP from parent doesn't
        # kill us when SessionEnd completes.
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        # Hook must exit cleanly even if spawn fails — don't block
        # SessionEnd on our own infrastructure.
        click.echo(f"stop-audit-spawn: {e}", err=True)


# ---------------------------------------------------------------------------
# intent subcommands — LLM intent eval + management
# ---------------------------------------------------------------------------

@cli.group()
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




