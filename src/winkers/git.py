"""Thin wrapper around git subprocess calls.

Centralises encoding, error handling, and Windows CREATE_NO_WINDOW
so callers don't repeat the same boilerplate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


AUTO_COMMIT_MARKER = "auto-commit"
"""Substring used to detect auto-commit messages (shared with CLI hook)."""


def run_git(
    args: list[str],
    cwd: Path | str,
    timeout: int = 10,
) -> str | None:
    """Run a git command and return stdout, or *None* on any failure.

    Handles:
    - ``encoding="utf-8"`` + ``errors="replace"`` (Windows codepage fix)
    - ``CREATE_NO_WINDOW`` on Windows
    - ``TimeoutExpired`` / ``FileNotFoundError``
    - Non-zero return codes
    - ``stdout is None`` edge case
    """
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": str(cwd),
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(["git", *args], **kwargs)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    return result.stdout or ""
