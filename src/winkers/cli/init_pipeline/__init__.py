"""Init-pipeline helpers — extracted from cli/main.py.

`winkers init` is a fat command — graph build + semantic enrichment +
LLM impact + per-unit descriptions + IDE auto-registration + history
snapshots + …. Putting every step in main.py made the file unreadable
(2k+ lines of helpers), so each step lives in a sub-module here:

  units.py        description-first units pipeline (Phase 1)
  semantic.py     semantic.json enrichment + interactive rule audit
  pipelines.py    impact / intent / debt / git history sub-pipelines
  bootstrap.py    dotenv, gitignore, paths, language detect, sessions
  install.py      IDE auto-registration (Claude Code, Cursor, generic)

Public surface: this `__init__.py` re-exports the helpers main.py uses,
so the CLI command body stays exactly the same shape.
"""

from winkers.cli.init_pipeline.bootstrap import (  # noqa: F401
    MAX_SNAPSHOTS,
    _backup_file,
    _detect_and_lock_language,
    _gc_runtime_sessions,
    _load_dotenv,
    _repair_sessions,
    _save_history_snapshot,
    _templates_dir,
    _update_gitignore,
    _winkers_bin,
)
from winkers.cli.init_pipeline.install import (  # noqa: F401
    WINKERS_TOOLS_PERMISSION,
    _autodetect_ide,
    _install_claude_code,
    _install_claude_md_snippet,
    _install_cursor,
    _install_generic,
    _install_interactive_hooks,
    _install_session_hook,
    _install_winkers_pointer,
    _is_winkers_managed_hook,
    _migrate_user_scope_mcp,
    _strip_managed_hooks,
    _strip_winkers_snippet,
    _upsert_hook,
)
from winkers.cli.init_pipeline.pipelines import (  # noqa: F401
    _collect_git_history,
    _intent_provider_ready,
    _read_fn_source,
    _run_debt_analysis,
    _run_impact_generation,
    _run_impact_only,
    _run_intent_generation,
)
from winkers.cli.init_pipeline.semantic import (  # noqa: F401
    _apply_audit,
    _interactive_review,
    _run_semantic_enrichment,
)
from winkers.cli.init_pipeline.units import (  # noqa: F401
    _author_meta_unit_descriptions,
    _run_units_pipeline,
    _value_unit_kind_from_collection,
)
