"""Audit runner — `claude --print` subprocess for read-only audit.

Same pattern as `winkers/descriptions/author.py`:
  - subscription auth (no API key)
  - cwd=tmp dir to dodge project-level Claude Code hooks
  - prompt via stdin to avoid Windows .cmd shim quoting issues

Difference from the description-author: allowedTools is `Read,Grep,Glob`
(read-only), and we do NOT cap max_tokens — the user explicitly
requested no cap on Phase 3 audit (long checklists are fine).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _resolve_claude_bin() -> str:
    """Pick the right claude executable.

    On Windows, the npm shim has a `.cmd` extension; `shutil.which`
    might return the extensionless POSIX shim which CreateProcess
    rejects. Prefer `claude.cmd` on Windows.
    """
    if sys.platform == "win32":
        cmd = shutil.which("claude.cmd")
        if cmd:
            return cmd
    return shutil.which("claude") or "claude"


_CLAUDE_BIN = _resolve_claude_bin()
_DEFAULT_TIMEOUT = 600  # seconds — long checklists allowed


def run_audit(
    prompt: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> str | None:
    """Spawn `claude --print` with read-only tools, return stdout.

    Returns None on subprocess failure or empty output. The caller
    is responsible for writing the result to `.winkers_pending.md`.

    cwd is forced to OS temp dir — running with cwd inside a Winkers
    project would activate that project's Claude Code hooks (auto-
    commit, prompt-enrich, etc.) for the nested call, which absorbs
    stdout and triggers git-index races (we observed this in Phase 1
    rollout). The audit doesn't need project cwd anyway.
    """
    cmd = [
        _CLAUDE_BIN,
        "--print",
        "--allowedTools", "Read,Grep,Glob",
    ]
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    tmp_cwd = tempfile.gettempdir()

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True, text=True,
            timeout=timeout_seconds,
            encoding="utf-8", errors="replace",
            cwd=tmp_cwd,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("audit timed out after %ds", timeout_seconds)
        return None
    except FileNotFoundError:
        log.error("claude binary not found at %r", _CLAUDE_BIN)
        return None

    output = (result.stdout or "").strip()
    if not output:
        log.warning(
            "audit returned empty stdout (exit=%d, stderr=%r)",
            result.returncode, (result.stderr or "")[:200],
        )
        return None
    return output
