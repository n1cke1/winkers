"""IDE auto-registration for `winkers init` — Claude Code, Cursor, generic AGENTS.md."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import click

from winkers import __version__
from winkers.cli.init_pipeline.bootstrap import _templates_dir, _winkers_bin
from winkers.git import AUTO_COMMIT_MARKER

WINKERS_TOOLS_PERMISSION = "mcp__winkers__*"


_SNIPPET_HEADING = "## Architectural context (Winkers)"
_SNIPPET_END_MARKER = "<!-- winkers-snippet-end -->"
_SNIPPET_VERSION_RE = re.compile(r"^<!-- winkers-snippet-version: [^>]+ -->\n", re.MULTILINE)


# Old semantic-summary markers — kept only for the one-shot migration that
# rewrites them into the new pointer block. New installs use _WINKERS_*.
_OLD_SEM_START = "<!-- winkers-semantic-start -->"
_OLD_SEM_END = "<!-- winkers-semantic-end -->"

_WINKERS_START = "<!-- winkers-start -->"
_WINKERS_END = "<!-- winkers-end -->"

_POINTER_BLOCK = (
    f"{_WINKERS_START}\n"
    "### Winkers\n\n"
    "This project uses Winkers (function-level dependency graph + semantic layer"
    " + coding rules). Before non-trivial edits call"
    " `orient(include=[\"map\",\"conventions\",\"rules_list\"])`"
    " for zones, data flow, domain context, and rules.\n"
    f"{_WINKERS_END}"
)


def _autodetect_ide(root: Path) -> None:
    """Detect IDE from project files and auto-register MCP server."""
    detected = False

    # Claude Code: .claude/ directory or CLAUDE.md
    if (root / ".claude").is_dir() or (root / "CLAUDE.md").exists():
        _install_claude_code(root)
        detected = True

    # Cursor: .cursor/ directory
    if (root / ".cursor").is_dir():
        _install_cursor(root)
        detected = True

    if not detected:
        click.echo(
            "  No IDE detected. To register manually:\n"
            "    winkers init  (with .claude/ or .cursor/ present)"
        )


def _install_claude_code(root: Path) -> None:
    # --- Project-level .mcp.json ---
    # Absolute root so the MCP server finds the project regardless of
    # the working directory Claude Code launches from. Absolute command
    # so headless/subprocess contexts with stripped-down PATH (systemd
    # services, ticket runners) can actually invoke winkers.
    mcp_json = root / ".mcp.json"
    root_posix = str(root).replace("\\", "/")
    winkers_bin = _winkers_bin().replace("\\", "/")
    mcp_config = {
        "mcpServers": {
            "winkers": {
                "command": winkers_bin,
                "args": ["serve", root_posix],
                "type": "stdio",
            }
        }
    }
    mcp_json.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
    click.echo(f"  [ok] MCP server registered (project): {mcp_json}")

    # --- Migrate user-scope entry in ~/.claude.json (if present) ---
    # Old behaviour was to *delete* the user-scope entry on every init. That
    # silently broke `claude --print` headless workflows (ticket runners,
    # scheduled agents) which skip the project-trust dialog and therefore
    # never see project-scope MCP. Now we keep an existing entry and just
    # refresh its command/args to the current binary + this root.
    _migrate_user_scope_mcp(root, winkers_bin, root_posix)

    _install_session_hook(root, winkers_bin)
    _install_claude_md_snippet(root)
    _install_winkers_pointer(root)


def _migrate_user_scope_mcp(root: Path, winkers_bin: str, root_posix: str) -> None:
    """Refresh — but never delete — the user-scope winkers MCP entry.

    Safe to call when no entry exists: it just no-ops. When an entry exists
    pointing at a stale path or the wrong project, update the command/args
    to the current binary + this root so `claude --print` keeps working.
    """
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return
    try:
        settings = json.loads(claude_json.read_text(encoding="utf-8"))
    except Exception:
        return
    entry = (settings.get("mcpServers") or {}).get("winkers")
    if not entry:
        return
    desired = {
        "type": "stdio",
        "command": winkers_bin,
        "args": ["serve", root_posix],
    }
    # Preserve any extra keys the user added (env, headers, etc.).
    merged = {**entry, **desired}
    if merged == entry:
        return
    settings.setdefault("mcpServers", {})["winkers"] = merged
    claude_json.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    click.echo(f"  [ok] Refreshed user-scope winkers MCP in {claude_json}")


def _strip_winkers_snippet(text: str) -> str:
    """Remove every prior Winkers snippet from CLAUDE.md, idempotently.

    Two block shapes are stripped:

    1. **New format** — bracketed by `<!-- winkers-snippet-version: X -->`
       and `<!-- winkers-snippet-end -->`. Stripped by markers, so user
       content right after the closing marker is preserved.
    2. **Legacy format** — one or more stacked version stamps followed
       by `## Architectural context (Winkers)` and content through the
       next `## ` heading or EOF. Older releases left these around,
       sometimes with multiple stacked stamps (Issue #2 in the
       2026-04-26 invoicekit feedback). The bleed risk only matters for
       stale legacy installs without an end marker; new installs pin the
       boundary explicitly.

    Orphan version stamps and a standalone heading-without-stamp are also
    swept so the file ends up with no winkers traces before re-insertion.
    """
    new_re = re.compile(
        r"<!-- winkers-snippet-version: [^>]+ -->\n"
        r".*?"
        + re.escape(_SNIPPET_END_MARKER)
        + r"\n?",
        re.DOTALL,
    )
    text = new_re.sub("", text)

    legacy_re = re.compile(
        r"(?:^<!-- winkers-snippet-version: [^>]+ -->\n)+"
        + re.escape(_SNIPPET_HEADING)
        + r".*?(?=^## |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    text = legacy_re.sub("", text)

    text = _SNIPPET_VERSION_RE.sub("", text)

    standalone_re = re.compile(
        r"^" + re.escape(_SNIPPET_HEADING) + r".*?(?=^## |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    text = standalone_re.sub("", text)

    # Strips can leave 3+ consecutive newlines where the block used to
    # sit; collapse so re-runs land on the same byte-for-byte output.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _install_claude_md_snippet(root: Path) -> None:
    """Append (or update) Winkers MCP usage instructions in CLAUDE.md.

    Strips any prior snippet — including stacked-version-stamp duplicates
    older releases used to leave behind — and inserts the canonical block
    fresh, bracketed by an end marker so subsequent strips have a hard
    boundary. Idempotent: skips the write when output matches existing.
    """
    claude_md = root / "CLAUDE.md"
    snippet = (_templates_dir() / "claude_code" / "claude_md_snippet.md").read_text(
        encoding="utf-8"
    )
    block = snippet.rstrip() + "\n" + _SNIPPET_END_MARKER + "\n"

    if not claude_md.exists():
        claude_md.write_text(block, encoding="utf-8")
        click.echo("  [ok] Added Winkers MCP instructions to CLAUDE.md")
        return

    existing = claude_md.read_text(encoding="utf-8")
    had_snippet = _SNIPPET_HEADING in existing or _SNIPPET_END_MARKER in existing
    cleaned = _strip_winkers_snippet(existing)

    first_h1 = re.search(r"^# .+\n", cleaned, re.MULTILINE)
    if first_h1:
        insert_at = first_h1.end()
        updated = cleaned[:insert_at] + "\n" + block + cleaned[insert_at:].lstrip("\n")
    else:
        updated = block + "\n" + cleaned.lstrip("\n")

    final = updated.rstrip() + "\n"
    if final == existing:
        click.echo("  [ok] CLAUDE.md Winkers section is up to date.")
        return

    claude_md.write_text(final, encoding="utf-8")
    if had_snippet:
        click.echo(f"  [ok] Updated Winkers section in CLAUDE.md to v{__version__}.")
    else:
        click.echo("  [ok] Added Winkers MCP instructions to CLAUDE.md")


def _install_winkers_pointer(root: Path) -> None:
    """Ensure CLAUDE.md has a single static Winkers pointer block.

    The pointer is a short pointer to `orient` — it does NOT duplicate
    semantic.json content. Orient is the single source of truth for
    data_flow / domain_context / zones / rules, so there is nothing to drift.

    Migration: older CLAUDE.md files may contain the deprecated
    `<!-- winkers-semantic-start -->` block with verbatim semantic data;
    we replace it in-place on the next run.
    """
    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        return

    existing = claude_md.read_text(encoding="utf-8")

    # One-shot migration of the old semantic-summary block.
    if _OLD_SEM_START in existing and _OLD_SEM_END in existing:
        s = existing.index(_OLD_SEM_START)
        e = existing.index(_OLD_SEM_END) + len(_OLD_SEM_END)
        updated = existing[:s] + _POINTER_BLOCK + existing[e:]
        claude_md.write_text(updated, encoding="utf-8")
        click.echo("  [ok] Migrated CLAUDE.md semantic block → Winkers pointer")
        return

    if _WINKERS_START in existing and _WINKERS_END in existing:
        s = existing.index(_WINKERS_START)
        e = existing.index(_WINKERS_END) + len(_WINKERS_END)
        # Idempotent — if block is already up to date, skip the write.
        if existing[s:e] == _POINTER_BLOCK:
            return
        updated = existing[:s] + _POINTER_BLOCK + existing[e:]
        claude_md.write_text(updated, encoding="utf-8")
        click.echo("  [ok] Refreshed CLAUDE.md Winkers pointer")
        return

    claude_md.write_text(
        existing.rstrip() + "\n\n" + _POINTER_BLOCK + "\n", encoding="utf-8"
    )
    click.echo("  [ok] Added Winkers pointer to CLAUDE.md")


def _is_winkers_managed_hook(command: str) -> bool:
    """True if a settings.json hook command was installed by winkers.

    Both signals required: the command invokes the winkers binary AND
    targets one of our managed verbs. Avoids wiping user-owned hooks
    that happen to mention 'winkers' for unrelated reasons.
    """
    cmd = command.lower()
    if "winkers" not in cmd:
        return False
    return any(verb in cmd for verb in ("hook ", "record", "autocommit"))


def _strip_managed_hooks(hooks: dict) -> bool:
    """Sweep every winkers-installed hook from settings before reinstall.

    Marker-update logic in earlier releases left stale paths behind on
    copied/migrated projects (Issue #3 in the 2026-04-26 invoicekit
    feedback): a project moved from `C:/orig` to `/tmp/copy` carried
    `command: "winkers hook pre-write C:/orig"` because the marker
    matched but the path-rewrite branch missed corner cases. Wiping
    first means the install pass below is unconditionally a fresh write.
    Returns True if anything was removed.
    """
    changed = False
    for event_name in list(hooks.keys()):
        event_hooks = hooks.get(event_name)
        if not isinstance(event_hooks, list):
            continue
        kept_entries: list = []
        for entry in event_hooks:
            if not isinstance(entry, dict):
                kept_entries.append(entry)
                continue
            inner = entry.get("hooks", [])
            if not isinstance(inner, list):
                kept_entries.append(entry)
                continue
            kept_inner = [h for h in inner if not _is_winkers_managed_hook(h.get("command", ""))]
            if len(kept_inner) != len(inner):
                changed = True
            if kept_inner:
                entry["hooks"] = kept_inner
                kept_entries.append(entry)
            # entry with empty hooks list is dropped
        if kept_entries:
            hooks[event_name] = kept_entries
        else:
            del hooks[event_name]
            changed = True
    return changed


def _install_session_hook(root: Path, winkers_bin: str) -> None:
    """Register SessionEnd hook + tool permissions in .claude/settings.json."""
    settings_path = root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    changed = False

    # --- SessionEnd hook ---
    hooks = settings.setdefault("hooks", {})

    # Wipe stale winkers hooks before reinstall — protects against stale
    # absolute paths (copied/migrated projects) and against legacy hook
    # names from older releases that the marker-based updater missed.
    if _strip_managed_hooks(hooks):
        click.echo("  [ok] Cleared stale winkers hooks before reinstall.")
        changed = True
    session_end = hooks.setdefault("SessionEnd", [])

    # Use forward slashes: Claude Code hooks run in Git Bash on Windows
    hook_bin = winkers_bin.replace("\\", "/")

    # --- autocommit hook (must run before record so bind_to_commit finds the commit) ---
    autocommit_cmd = (
        f"git add -A && git diff --cached --quiet"
        f" || {hook_bin} autocommit"
    )
    changed = _upsert_hook(
        session_end, AUTO_COMMIT_MARKER, autocommit_cmd,
        timeout=30, label="SessionEnd autocommit",
    ) or changed

    # --- record hook ---
    record_cmd = f"{hook_bin} record --hook"
    changed = _upsert_hook(
        session_end, "record", record_cmd,
        timeout=60, label="SessionEnd record",
    ) or changed

    # --- Tool permissions ---
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])

    if WINKERS_TOOLS_PERMISSION not in allow:
        allow.append(WINKERS_TOOLS_PERMISSION)
        click.echo(f"  [ok] Tool permissions added: {WINKERS_TOOLS_PERMISSION}")
        changed = True
    else:
        click.echo("  [ok] Tool permissions already set.")

    # --- Interactive hooks (before_create, duplicate gate, impact_check) ---
    changed = _install_interactive_hooks(hooks, hook_bin, root) or changed

    if changed:
        settings_path.write_text(
            json.dumps(settings, indent=2), encoding="utf-8",
        )


def _upsert_hook(
    hook_list: list[dict], marker: str, command: str,
    timeout: int = 10, label: str = "",
) -> bool:
    """Insert or update a hook entry. Returns True if changed."""
    for entry in hook_list:
        for h in entry.get("hooks", []):
            if marker in h.get("command", ""):
                if h["command"] == command:
                    click.echo(f"  [ok] {label} hook up to date.")
                    return False
                # Path changed — update in place
                h["command"] = command
                click.echo(f"  [ok] {label} hook path updated.")
                return True

    # Not found — add new
    hook_list.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    })
    click.echo(f"  [ok] {label} hook installed.")
    return True


def _install_interactive_hooks(hooks: dict, hook_bin: str, root: Path) -> bool:
    """Install UserPromptSubmit, PreToolUse, PostToolUse, Stop hooks."""
    changed = False
    root_posix = str(root).replace("\\", "/")

    hook_defs = [
        {
            "event": "UserPromptSubmit",
            "marker": "prompt-enrich",
            "command": f"{hook_bin} hook prompt-enrich {root_posix}",
            "timeout": 2,
            "matcher": "",
            "label": "prompt-enrich",
        },
        {
            "event": "PreToolUse",
            "marker": "pre-write",
            "command": f"{hook_bin} hook pre-write {root_posix}",
            "timeout": 3,
            "matcher": "Write|Edit|MultiEdit",
            "label": "pre-write duplicate gate",
        },
        {
            "event": "PostToolUse",
            "marker": "post-write",
            "command": f"{hook_bin} hook post-write {root_posix}",
            "timeout": 30,
            "matcher": "Write|Edit|MultiEdit",
            "label": "post-write impact check",
        },
        # Phase 3 — coherence audit lifecycle.
        # SessionStart records the git HEAD baseline so SessionEnd's
        # audit can compute an honest diff (not just HEAD~1 vs HEAD).
        {
            "event": "SessionStart",
            "marker": "session-start",
            "command": f"{hook_bin} hook session-start {root_posix}",
            "timeout": 2,
            "matcher": "",
            "label": "session-start baseline",
        },
        # SessionEnd uses the *spawn* variant — it detaches the slow
        # `claude --print` audit subprocess and returns immediately so
        # the session-end completion isn't blocked. The actual audit
        # runs out-of-band and writes `.winkers_pending.md`.
        {
            "event": "SessionEnd",
            "marker": "stop-audit-spawn",
            "command": f"{hook_bin} hook stop-audit-spawn {root_posix}",
            "timeout": 5,
            "matcher": "",
            "label": "stop-audit (detached)",
        },
    ]

    # 0.8.1: session_done muted — remove legacy Stop/session-audit hook if present.
    stop_hooks = hooks.get("Stop", [])
    filtered_stop: list = []
    for entry in stop_hooks:
        kept = [h for h in entry.get("hooks", []) if "session-audit" not in h.get("command", "")]
        if kept:
            entry["hooks"] = kept
            filtered_stop.append(entry)
        elif entry.get("hooks"):
            changed = True
            click.echo("  [ok] Stop session-audit hook removed (session_done muted in 0.8.1).")
    if "Stop" in hooks:
        if filtered_stop:
            hooks["Stop"] = filtered_stop
        else:
            del hooks["Stop"]

    for hdef in hook_defs:
        event_hooks = hooks.setdefault(hdef["event"], [])
        label = f"{hdef['event']} {hdef['label']}"

        # Check if exists and path is current
        found = False
        for entry in event_hooks:
            for h in entry.get("hooks", []):
                if hdef["marker"] in h.get("command", ""):
                    found = True
                    if h["command"] != hdef["command"]:
                        h["command"] = hdef["command"]
                        click.echo(f"  [ok] {label} hook path updated.")
                        changed = True
                    else:
                        click.echo(f"  [ok] {label} hook up to date.")
                    break
            if found:
                break

        if not found:
            entry_new: dict = {
                "hooks": [{
                    "type": "command",
                    "command": hdef["command"],
                    "timeout": hdef["timeout"],
                }],
            }
            if hdef["matcher"]:
                entry_new["matcher"] = hdef["matcher"]
            event_hooks.append(entry_new)
            click.echo(f"  [ok] {label} hook installed.")
            changed = True

    return changed


def _install_cursor(root: Path) -> None:
    templates = _templates_dir() / "cursor"
    rules_dst = root / ".cursor" / "rules"
    rules_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(templates / "winkers.mdc", rules_dst / "winkers.mdc")
    click.echo(f"  [ok] Cursor rules installed: {rules_dst / 'winkers.mdc'}")


def _install_generic(root: Path) -> None:
    templates = _templates_dir() / "generic"
    snippet = (templates / "AGENTS.md").read_text(encoding="utf-8")
    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        existing = agents_md.read_text(encoding="utf-8")
        if "Winkers" not in existing:
            agents_md.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
            click.echo(f"  [ok] Appended Winkers snippet to {agents_md}")
        else:
            click.echo("  ~ AGENTS.md already mentions Winkers, skipped.")
    else:
        agents_md.write_text(snippet, encoding="utf-8")
        click.echo(f"  [ok] Created {agents_md}")
