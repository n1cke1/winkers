"""Commit message formatting — template + prepare-commit-msg hook + autocommit."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC
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
    Available variables: {message}, {ticket}, {date}, {datetime}, {author}.
    """
    import datetime as _dt
    import subprocess

    ticket = ""
    match = re.search(ticket_pattern, message)
    if match:
        ticket = match.group(0)
        # Remove ticket from message, handling conventional commit scope: feat(TICKET): msg
        pre = message[:match.start()]
        post = message[match.end():]
        if pre.endswith("(") and post.startswith(")"):
            pre = pre[:-1]
            post = post[1:]
        # Join without extra space when post starts with punctuation
        message = pre.rstrip() + post.lstrip(" ")
        message = message.strip(" -:[]")

    try:
        author = subprocess.check_output(
            ["git", "config", "user.name"], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        author = ""

    now = _dt.datetime.now()
    date = now.strftime("%Y-%m-%d")
    dt = now.strftime("%Y-%m-%d %H:%M")

    result = template.format(
        message=message,
        ticket=ticket,
        date=date,
        author=author,
        datetime=dt,
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


# ---------------------------------------------------------------------------
# Autocommit — AI-generated commit messages
# ---------------------------------------------------------------------------

AUTOCOMMIT_PROMPT = """\
You are generating a one-line git commit message.
Respond with ONLY the commit message, nothing else.

Rules:
- Start with a type: feat, fix, refactor, docs, test, chore, style
- Max 72 characters
- Describe WHAT changed and WHY, not HOW
- Use imperative mood ("add", "fix", not "added", "fixed")
- If the diff is unclear, describe the files changed
"""


def _git_diff_summary(root: Path) -> str:
    """Get staged diff stat + first 80 lines of actual diff."""
    try:
        stat = subprocess.check_output(
            ["git", "diff", "--cached", "--stat"],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        stat = ""

    try:
        diff = subprocess.check_output(
            ["git", "diff", "--cached", "-U2"],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
        # Truncate large diffs
        lines = diff.splitlines()
        if len(lines) > 80:
            diff = "\n".join(lines[:80]) + f"\n... ({len(lines) - 80} more lines)"
    except Exception:
        diff = ""

    return f"## Diff stat\n{stat}\n\n## Diff\n{diff}" if stat else ""


def _git_changed_files(root: Path) -> list[str]:
    """Get list of staged file names."""
    try:
        output = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
        return output.splitlines() if output else []
    except Exception:
        return []


def _changed_functions(root: Path, files: list[str]) -> list[str]:
    """Find function names in changed files from the graph."""
    from winkers.store import GraphStore
    graph = GraphStore(root).load()
    if not graph:
        return []
    fn_names = []
    for f in files:
        fnode = graph.files.get(f)
        if fnode:
            for fid in fnode.function_ids[:5]:
                fn = graph.functions.get(fid)
                if fn:
                    fn_names.append(fn.name)
    return fn_names


def _fallback_message(root: Path) -> str:
    """Generate a commit message from file names + functions (no API)."""
    files = _git_changed_files(root)
    if not files:
        return "wip: changes"
    fn_names = _changed_functions(root, files)

    short_files = [f.split("/")[-1] for f in files[:4]]
    msg = "wip: " + ", ".join(short_files)
    if len(files) > 4:
        msg += f" (+{len(files) - 4} more)"
    if fn_names:
        msg += " | " + ", ".join(fn_names[:3])
    # Truncate to 72 chars
    if len(msg) > 72:
        msg = msg[:69] + "..."
    return msg


def generate_commit_message(root: Path, api_key: str | None = None) -> str:
    """Generate a commit message via Haiku, with fallback to file/function list."""
    diff_summary = _git_diff_summary(root)
    if not diff_summary:
        return _fallback_message(root)

    # Try API
    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not resolved_key:
        return _fallback_message(root)

    try:
        import anthropic
    except ImportError:
        return _fallback_message(root)

    try:
        from winkers.semantic import _build_http_client
        http_client = _build_http_client()
        kwargs: dict = {"api_key": resolved_key}
        if http_client:
            kwargs["http_client"] = http_client
        client = anthropic.Anthropic(**kwargs)

        model = os.environ.get("WINKERS_AUTOCOMMIT_MODEL", "claude-haiku-4-5-20251001")
        response = client.messages.create(
            model=model,
            max_tokens=100,
            system=AUTOCOMMIT_PROMPT,
            messages=[{"role": "user", "content": diff_summary}],
        )
        msg = response.content[0].text.strip().strip('"').strip("'")
        # Sanitize: one line, max 72 chars
        msg = msg.splitlines()[0]
        if len(msg) > 72:
            msg = msg[:69] + "..."
        return msg
    except Exception:
        return _fallback_message(root)


def enrich_commit(
    root: Path, commit_hash: str, api_key: str | None = None,
) -> str | None:
    """Generate a better message for an existing commit using its diff + session context."""
    # Get diff for this commit
    try:
        stat = subprocess.check_output(
            ["git", "show", "--stat", "--format=", commit_hash],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
        diff = subprocess.check_output(
            ["git", "show", "-U2", "--format=", commit_hash],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
        lines = diff.splitlines()
        if len(lines) > 80:
            diff = "\n".join(lines[:80]) + f"\n... ({len(lines) - 80} more lines)"
    except Exception:
        return None

    if not stat:
        return None

    # Try to find matching session by commit date
    session_context = ""
    try:
        commit_date = subprocess.check_output(
            ["git", "show", "--format=%aI", "-s", commit_hash],
            text=True, cwd=str(root), stderr=subprocess.DEVNULL,
        ).strip()
        session_context = _find_session_context(root, commit_date)
    except Exception:
        pass

    prompt = f"## Diff stat\n{stat}\n\n## Diff\n{diff}"
    if session_context:
        prompt += f"\n\n## Session context\n{session_context}"

    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not resolved_key:
        return None

    try:
        import anthropic

        from winkers.semantic import _build_http_client
        http_client = _build_http_client()
        kwargs: dict = {"api_key": resolved_key}
        if http_client:
            kwargs["http_client"] = http_client
        client = anthropic.Anthropic(**kwargs)

        model = os.environ.get("WINKERS_AUTOCOMMIT_MODEL", "claude-haiku-4-5-20251001")
        response = client.messages.create(
            model=model,
            max_tokens=100,
            system=AUTOCOMMIT_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        msg = response.content[0].text.strip().strip('"').strip("'")
        return msg.splitlines()[0][:72]
    except Exception:
        return None


def _find_session_context(root: Path, commit_date: str) -> str:
    """Find a recorded session closest to the commit date."""
    from winkers.session_store import SessionStore
    sessions = SessionStore(root).load_all()
    if not sessions:
        return ""

    # Find session whose completed_at is closest to commit_date
    best = None
    best_delta = float("inf")
    for s in sessions:
        try:
            from datetime import datetime
            commit_dt = datetime.fromisoformat(commit_date)
            session_dt = datetime.fromisoformat(s.session.completed_at)
            # Normalize to UTC if needed
            if commit_dt.tzinfo is None:
                commit_dt = commit_dt.replace(tzinfo=UTC)
            if session_dt.tzinfo is None:
                session_dt = session_dt.replace(tzinfo=UTC)
            delta = abs((commit_dt - session_dt).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best = s
        except Exception:
            continue

    # Only match if within 30 minutes
    if best and best_delta < 1800:
        return (
            f"Task: {best.session.task_prompt}\n"
            f"Files modified: {best.session.files_modified}\n"
            f"Score: {best.score}"
        )
    return ""
