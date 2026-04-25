"""Description-author runner via `claude --print` subprocess.

Per the project's subscription-first rule, description generation goes
through `claude --print -p ...` (uses Claude subscription auth), not the
Anthropic API SDK. Each call costs $0 on a Pro/Max plan.

Sequential by default — concurrency can wrongly hit subscription rate
limits. The runner is one-call-per-unit; orchestration (which units,
in what order) is handled by callers.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from winkers.descriptions.models import Description, parse_description_response
from winkers.descriptions.prompts import (
    format_data_file_prompt,
    format_function_prompt,
    format_template_section_prompt,
)

log = logging.getLogger(__name__)


def _resolve_claude_bin() -> str:
    """Resolve the claude executable, preferring Windows .cmd shim.

    npm installs both `claude` (POSIX shim, no extension) and `claude.cmd`
    (Windows batch shim) under the same directory. `shutil.which("claude")`
    can return the extensionless one first, which Windows CreateProcess
    rejects with `WinError 193`. On Windows we explicitly look for the
    .cmd variant first.
    """
    if sys.platform == "win32":
        cmd = shutil.which("claude.cmd")
        if cmd:
            return cmd
    return shutil.which("claude") or "claude"


_CLAUDE_BIN = _resolve_claude_bin()
_DEFAULT_TIMEOUT = 180  # seconds — long enough for cold model + 100-word output


def author_function_description(
    fn_source: str,
    file_path: str,
    fn_name: str,
    callers: list[str] | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> Description | None:
    """Generate a description for one function unit. Returns None on failure."""
    prompt = format_function_prompt(fn_source, file_path, fn_name, callers)
    return _run_claude(prompt, cwd=cwd, timeout=timeout_seconds)


def author_data_file_description(
    file_content: str,
    file_path: str,
    cwd: str | Path | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> Description | None:
    """Generate a description for one data file. Returns None on failure."""
    prompt = format_data_file_prompt(file_content, file_path)
    return _run_claude(prompt, cwd=cwd, timeout=timeout_seconds)


def author_template_description(
    section_html: str,
    file_path: str,
    section_id: str,
    leading_comment: str = "",
    neighbor_section_ids: list[str] | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> Description | None:
    """Generate a description for one template section. Returns None on failure."""
    prompt = format_template_section_prompt(
        section_html, file_path, section_id,
        leading_comment, neighbor_section_ids,
    )
    return _run_claude(prompt, cwd=cwd, timeout=timeout_seconds)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _run_claude(
    prompt: str,
    cwd: str | Path | None,
    timeout: int,
) -> Description | None:
    """Spawn `claude --print -p <prompt>` and parse the JSON output.

    The subprocess runs with no tools allowed (`--allowedTools ""`) — the
    description-author task is pure prompt-completion, no file I/O needed.
    This keeps the call cheap and prevents the child agent from doing
    unrelated work.

    Drops `CLAUDECODE` from env so a description-author call from inside
    a Claude Code session doesn't trigger nested-CLI guards. Same approach
    as `ticket_service_runner.py` (which already does this in production).
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    # Pass prompt via stdin, not `-p <prompt>`. On Windows, the npm .cmd
    # shim mangles long prompts with special chars (backticks, newlines,
    # angle brackets) when they pass through cmd.exe quoting. stdin
    # bypasses cmd quoting entirely and works the same on POSIX.
    cmd = [_CLAUDE_BIN, "--print", "--allowedTools", ""]

    # Description-author prompts are SELF-CONTAINED — function source is
    # in the prompt body, no file I/O needed. Running with cwd inside a
    # project that has Winkers `.claude/settings.json` hooks
    # (UserPromptSubmit / SessionEnd / Pre+PostToolUse) makes Claude
    # invoke those hooks for every nested call, which absorbs stdout and
    # triggers git-index races during parallel runs.
    #
    # cwd=None inherits the parent process's cwd — and the parent is
    # `winkers init` running INSIDE the project root, so we'd still
    # trigger the project hooks. Force cwd to OS temp dir to escape.
    import tempfile
    tmp_cwd = tempfile.gettempdir()

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True, text=True,
            timeout=timeout,
            encoding="utf-8", errors="replace",
            cwd=tmp_cwd,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("description-author timed out after %ds", timeout)
        return None
    except FileNotFoundError:
        log.error(
            "claude binary not found at %r — install Claude Code or set "
            "the binary on PATH",
            _CLAUDE_BIN,
        )
        return None

    output = (result.stdout or "").strip()
    if not output:
        stderr_excerpt = (result.stderr or "").strip()[:200]
        log.warning(
            "description-author returned empty stdout (exit=%d, stderr=%r)",
            result.returncode, stderr_excerpt,
        )
        return None

    parsed = parse_description_response(output)
    if parsed is None:
        log.warning(
            "description-author output unparseable (first 200 chars): %r",
            output[:200],
        )
    return parsed
