"""Winkers CLI."""

from __future__ import annotations

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



# All concrete CLI commands live under `cli/commands/`. Register them after
# the root `cli` group is fully constructed.
from winkers.cli.commands import register_commands  # noqa: E402
from winkers.cli.commands.record import _check_redo  # noqa: E402, F401  (test re-export)

register_commands(cli)
