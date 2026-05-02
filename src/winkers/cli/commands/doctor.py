"""winkers doctor."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from winkers.cli.init_pipeline import (
    _load_dotenv,
)
from winkers.store import GraphStore


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


@click.command()
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
            # Check position: snippet should be in the first half. The
            # heading text changed across snippet versions ("Winkers —
            # coding agent helper" → "Architectural context (Winkers)"),
            # so anchor on the stable start-marker comment instead.
            marker_pos = content.find("<!-- winkers-snippet-version:")
            if marker_pos >= 0 and marker_pos > len(content) // 2:
                warn(
                    "CLAUDE.md Winkers section is near the end"
                    " — run: winkers init to move it up"
                )
            elif marker_pos >= 0:
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
