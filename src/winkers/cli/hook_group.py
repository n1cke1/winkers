"""`winkers hook ...` — Claude Code hook handlers (stdin JSON → stdout JSON).

Each subcommand is a thin wrapper around the corresponding handler in
`winkers.hooks.*`. They aren't called by users directly — Claude Code
invokes them per the `hooks` block in `.claude/settings.json` (set up
by `winkers init`'s `_install_session_hook` / `_install_interactive_hooks`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click


@click.group()
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

    root = Path(path).resolve()

    # Read session_id from Claude Code's hook payload before detaching;
    # the child runs with stdin=DEVNULL so we forward via env.
    session_id = ""
    try:
        payload = sys.stdin.read()
        if payload:
            session_id = str(json.loads(payload).get("session_id", ""))
    except Exception:
        session_id = ""

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

    child_env = os.environ.copy()
    if session_id:
        child_env["WINKERS_SESSION_ID"] = session_id

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(root),
        "env": child_env,
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
