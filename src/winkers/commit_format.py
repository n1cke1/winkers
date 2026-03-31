"""Commit message formatting — template + prepare-commit-msg hook."""

from __future__ import annotations

import json
import re
from pathlib import Path

from winkers.store import STORE_DIR

CONFIG_FILE = "config.json"

DEFAULT_TEMPLATE = "{message}"
DEFAULT_TICKET_PATTERN = r"[A-Z]+-\d+"

HOOK_SCRIPT = '''\
#!/bin/sh
# Winkers prepare-commit-msg hook
# Applies commit_format template from .winkers/config.json

COMMIT_MSG_FILE="$1"
COMMIT_SOURCE="$2"

# Only format manual commits (not merges, squashes, etc.)
if [ -n "$COMMIT_SOURCE" ]; then
    exit 0
fi

CONFIG=".winkers/config.json"
if [ ! -f "$CONFIG" ]; then
    exit 0
fi

# Delegate to winkers for the actual formatting
winkers commit-fmt "$COMMIT_MSG_FILE" 2>/dev/null || true
'''


def load_commit_format(root: Path) -> dict:
    """Load commit_format from .winkers/config.json."""
    config_path = root / STORE_DIR / CONFIG_FILE
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return config.get("commit_format", {})
    except Exception:
        return {}


def save_commit_format(root: Path, template: str, ticket_pattern: str) -> None:
    """Save commit_format to .winkers/config.json (merge with existing)."""
    config_path = root / STORE_DIR / CONFIG_FILE
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    config["commit_format"] = {
        "template": template,
        "ticket_pattern": ticket_pattern,
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def format_message(message: str, template: str, ticket_pattern: str) -> str:
    """Apply template to a commit message.

    Extracts ticket from message (if matches pattern), then fills template.
    Available variables: {message}, {ticket}, {date}, {author}.
    """
    import datetime
    import subprocess

    ticket = ""
    match = re.search(ticket_pattern, message)
    if match:
        ticket = match.group(0)
        # Remove ticket from message to avoid duplication
        message = message[:match.start()].strip() + message[match.end():].strip()
        message = message.strip(" -:[]")

    try:
        author = subprocess.check_output(
            ["git", "config", "user.name"], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        author = ""

    date = datetime.date.today().isoformat()

    result = template.format(
        message=message,
        ticket=ticket,
        date=date,
        author=author,
    )
    # Clean up empty placeholders if ticket/author missing
    result = re.sub(r"\[\]\s*", "", result)
    result = re.sub(r"\|\s*\|", "|", result)
    result = re.sub(r"\s*\|\s*$", "", result)
    return result.strip()


def install_hook(root: Path) -> Path:
    """Install prepare-commit-msg hook in .githooks/."""
    hooks_dir = root / ".githooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "prepare-commit-msg"
    hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")
    # Make executable (no-op on Windows but correct for cross-platform)
    hook_path.chmod(0o755)
    return hook_path


def normalize_commits(root: Path, git_range: str, dry_run: bool = True) -> list[dict]:
    """Normalize commit messages in a range using the configured template.

    Returns list of {hash, old_message, new_message} dicts.
    """
    import subprocess

    fmt = load_commit_format(root)
    template = fmt.get("template", DEFAULT_TEMPLATE)
    ticket_pattern = fmt.get("ticket_pattern", DEFAULT_TICKET_PATTERN)

    try:
        log_output = subprocess.check_output(
            ["git", "log", "--format=%H %s", git_range],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return []

    if not log_output:
        return []

    results = []
    for line in log_output.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        commit_hash, old_msg = parts
        new_msg = format_message(old_msg, template, ticket_pattern)
        if new_msg != old_msg:
            results.append({
                "hash": commit_hash[:8],
                "old": old_msg,
                "new": new_msg,
            })

    return results
