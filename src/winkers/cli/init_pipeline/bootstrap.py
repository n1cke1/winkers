"""Bootstrap helpers for `winkers init` — env, paths, gitignore, sessions, history."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import click

MAX_SNAPSHOTS = 20


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
    return Path(__file__).parent.parent.parent / "templates"


def _winkers_bin() -> str:
    """Resolve an absolute path to the winkers binary for hooks/MCP configs.

    Priority: active venv → sys.argv[0] → PATH → bare name. The first three
    yield an absolute path; the bare-name fallback is reserved for unusual
    environments where neither sys.argv[0] nor PATH lookup work.

    Why absolute matters: hook commands and `.mcp.json:command` are run by
    Claude Code in subprocess contexts (systemd services, headless ticket
    runners) whose PATH often lacks the venv's bin/ — so a bare "winkers"
    silently fails. Confirmed in tespy's prod runner on 2026-04-26.
    """
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        for candidate in (Path(venv) / "bin" / "winkers", Path(venv) / "Scripts" / "winkers.exe"):
            if candidate.exists():
                return str(candidate)
    if sys.argv and sys.argv[0]:
        argv0 = Path(sys.argv[0])
        if argv0.is_absolute() and argv0.exists():
            return str(argv0)
    via_path = shutil.which("winkers")
    if via_path:
        return via_path
    return "winkers"


def _detect_and_lock_language(root: Path) -> None:
    """Lock English as the description-authoring language by default.

    Universal English wins for retrieval — BGE-M3 has more weight on
    English data and domain terms are usually English regardless of
    project. The lock is idempotent: if the user has set
    `[project].language` already (even to a non-English value), we
    keep their choice. To re-detect dominant project language manually,
    `winkers.project_config.detect_project_language(root)` is exposed
    as a helper.
    """
    try:
        from winkers.project_config import save_project_language
        # save_project_language is a no-op if `[project].language` is
        # already set, so this just seeds new projects.
        save_project_language(root, "en")
    except Exception:
        pass


def _repair_sessions(root: Path) -> None:
    """Fix mojibake in commit messages caused by missing encoding='utf-8'."""
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


def _gc_runtime_sessions(root: Path) -> None:
    """Sweep stale per-Claude-session runtime dirs (hooks.log, audit.json, ...)."""
    try:
        from winkers.session.session_dir import gc_old_sessions
        removed = gc_old_sessions(root)
        if removed:
            click.echo(f"  Cleaned up {removed} stale session director(y/ies).")
    except Exception:
        # GC must never block init.
        pass


def _save_history_snapshot(root: Path, graph) -> None:
    """Save a timestamped copy of graph.json to .winkers/history/."""
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
    if not src.exists():
        return
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    shutil.copy2(src, history_dir / f"{prefix}-{ts}.json")
    snapshots = sorted(history_dir.glob(f"{prefix}-*.json"))
    if len(snapshots) > MAX_SNAPSHOTS:
        for old in snapshots[:-MAX_SNAPSHOTS]:
            old.unlink()
