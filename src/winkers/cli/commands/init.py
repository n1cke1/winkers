"""winkers init."""

from __future__ import annotations

from pathlib import Path

import click

from winkers.cli.init_pipeline import (
    _autodetect_ide,
    _collect_git_history,
    _detect_and_lock_language,
    _gc_runtime_sessions,
    _repair_sessions,
    _run_debt_analysis,
    _run_impact_generation,
    _run_impact_only,
    _run_intent_generation,
    _run_semantic_enrichment,
    _run_units_pipeline,
    _save_history_snapshot,
    _update_gitignore,
)
from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore


@click.command()
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
