"""Shared env loading for adapters.

Key file lives OUTSIDE invoicekit (pydantic-settings on that project is strict
and rejects unknown keys, so .env colocated with invoicekit breaks pytest).
We keep the secret at graph-mcp-bench/.wnk_env and inject it into winkers
subprocesses via env= at call time.
"""
from __future__ import annotations

import os
from pathlib import Path

_KEY_FILE = Path(__file__).resolve().parent.parent / ".wnk_env"


def winkers_env() -> dict[str, str]:
    """Return an env dict suitable for subprocess.run(..., env=<this>).

    Starts from os.environ, then overlays ANTHROPIC_API_KEY from .wnk_env
    if present. Returned dict is safe to pass to child processes — they
    will see the key, but the parent process / other children do not.
    """
    env = dict(os.environ)
    if _KEY_FILE.exists():
        for line in _KEY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    # Force UTF-8 stdout in winkers subprocesses — `winkers init` (>=0.8.4)
    # prints `≥3` which crashes on cp1251 consoles and aborts the whole
    # bench run via non-zero exit.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env
