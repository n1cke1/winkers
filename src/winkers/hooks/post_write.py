"""PostToolUse(Write|Edit) hook — impact check on file writes.

Runs graph update + impact analysis + coherence check automatically. Equivalent
to the `impact_check` MCP tool but triggered by Claude Code hooks protocol.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def run(root: Path) -> None:
    """Read hook JSON from stdin, run impact_check logic, output context."""
    try:
        hook_data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = hook_data.get("tool_name", "")
    tool_input = hook_data.get("tool_input", {})

    if tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    # Normalize to relative path
    try:
        rel_path = str(Path(file_path).relative_to(root)).replace("\\", "/")
    except ValueError:
        rel_path = file_path.replace("\\", "/")

    # Skip non-code files
    if not _is_code_file(rel_path):
        sys.exit(0)

    from winkers.detection.impact import compute_diff, snapshot_signatures
    from winkers.session.state import SessionStore, Warning, WriteEvent
    from winkers.store import GraphStore
    from winkers.value_locked import diff_collections

    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        sys.exit(0)

    # 1. Snapshot old signatures + value_locked collections
    old_sigs = snapshot_signatures(graph, [rel_path])
    old_value_locked = [c.model_copy(deep=True) for c in graph.value_locked_collections]

    # 2. Incremental graph update (refreshes value_locked too)
    store.update_files(graph, [rel_path])
    store.save(graph)

    # 3. Impact analysis
    diff = compute_diff(old_sigs, graph, [rel_path])
    value_changes = diff_collections(old_value_locked, graph.value_locked_collections)

    # 4. Coherence check
    coherence = _coherence_check(rel_path, root)

    # 5. Session state update
    session_store = SessionStore(root)
    session = session_store.load_or_create()

    event = WriteEvent(
        timestamp=datetime.now(UTC).isoformat(),
        file_path=rel_path,
        functions_added=[fn.name for fn in diff.added],
        functions_modified=[sc.fn.name for sc in diff.signature_changed],
        functions_removed=diff.removed,
        signature_changes=[
            {"fn_id": sc.fn_id, "old_sig": sc.old_signature, "new_sig": sc.new_signature}
            for sc in diff.signature_changed
        ],
    )
    session.add_write(event)

    for sc in diff.signature_changed:
        if sc.callers:
            session.add_warning(Warning(
                kind="broken_caller",
                severity="error",
                target=sc.fn_id,
                detail=(
                    f"{sc.fn.name}() signature changed: {sc.old_signature} → {sc.new_signature}. "
                    f"{len(sc.callers)} caller(s) may need updating."
                ),
            ))

    for rule in coherence:
        session.add_warning(Warning(
            kind="coherence",
            severity="warning",
            target=rel_path,
            detail=f"Rule #{rule['id']} \"{rule['title']}\": check {', '.join(rule['sync_with'])}",
            fix_approach=rule.get("fix_approach", "sync"),
        ))

    for vc in value_changes:
        if not vc["removed"]:
            continue
        session.add_warning(Warning(
            kind="value_locked",
            severity="error" if vc["affected_literal_uses"] > 0 else "warning",
            target=f"{vc['file']}::{vc['name']}",
            detail=(
                f"{vc['name']}: removed {vc['removed']!r}; "
                f"{vc['affected_literal_uses']} caller literal use(s) at risk."
            ),
        ))

    session_store.save(session)

    # Build additional context
    lines = [f"[Winkers] Graph updated: {rel_path}"]

    if diff.added:
        names = ", ".join(fn.name for fn in diff.added)
        lines.append(f"  NEW: {names}")

    if diff.signature_changed:
        for sc in diff.signature_changed:
            lines.append(
                f"  MODIFIED: {sc.fn.name}() — {sc.old_signature} → {sc.new_signature}"
            )
            if sc.callers:
                lines.append(f"    ⚠ {len(sc.callers)} caller(s) may need updating")

    if diff.removed:
        lines.append(f"  REMOVED: {', '.join(diff.removed)}")

    if coherence:
        for rule in coherence:
            sync = ", ".join(rule["sync_with"])
            lines.append(f"  COHERENCE: Rule #{rule['id']} \"{rule['title']}\" — check {sync}")

    for vc in value_changes:
        if not vc["removed"]:
            continue
        lines.append(
            f"  VALUE_LOCKED: {vc['name']} removed {vc['removed']} — "
            f"{vc['affected_literal_uses']} caller literal use(s) at risk"
        )

    pending = session.pending_warnings()
    if pending:
        lines.append(
            f"  SESSION: {len(pending)} warning(s) pending."
            " Call session_done() for an optional final audit."
        )

    if len(lines) > 1:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(lines),
            }
        }
        print(json.dumps(output))

    sys.exit(0)


_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".cs",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
})


def _is_code_file(path: str) -> bool:
    return Path(path).suffix.lower() in _CODE_EXTENSIONS


def _coherence_check(file_path: str, root: Path) -> list[dict]:
    """Find coherence rules where 'affects' matches the modified file."""
    from winkers.conventions import RulesStore

    rules_file = RulesStore(root).load()
    matches: list[dict] = []

    for r in rules_file.rules:
        if r.category != "coherence":
            continue
        if not any(file_path == a or file_path.endswith(a) for a in r.affects):
            continue
        entry: dict = {
            "id": r.id,
            "title": r.title,
            "sync_with": r.sync_with,
            "fix_approach": r.fix_approach or "sync",
        }
        matches.append(entry)

    return matches
