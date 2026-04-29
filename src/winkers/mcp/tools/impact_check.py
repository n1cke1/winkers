"""MCP tool: impact_check — incremental graph update + impact + coherence on file write."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from pathlib import Path

from mcp.types import Tool

from winkers.models import Graph

TOOL = Tool(
    name="impact_check",
    description=(
        "Call after writing, editing, or deleting code. Updates the project"
        " graph and checks for issues. Returns impact analysis for changed"
        " functions, coherence checklist, and session status summary."
        " In Claude Code this runs automatically via the post-write hook;"
        " call explicitly for re-check or files you didn't edit directly."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to the modified file",
            },
        },
        "required": ["file_path"],
    },
)


def _tool_impact_check(
    graph: Graph, args: dict, root: Path,
    get_graph: Callable[[], Graph | None],
) -> dict:
    from datetime import datetime

    from winkers.detection.impact import compute_diff, format_impact, snapshot_signatures
    from winkers.session.state import SessionStore, Warning, WriteEvent
    from winkers.store import GraphStore

    file_path = args.get("file_path", "")
    if not file_path:
        return {"error": "Provide 'file_path' — relative path to the modified file."}

    # Normalize path separators
    file_path = file_path.replace("\\", "/")

    # 1. Snapshot old signatures + value_locked collections before update
    old_sigs = snapshot_signatures(graph, [file_path])
    old_value_locked = [c.model_copy(deep=True) for c in graph.value_locked_collections]

    # 2. Incremental graph update
    store = GraphStore(root)
    store.update_files(graph, [file_path])
    store.save(graph)

    # Invalidate search token cache for updated file
    from winkers.search import invalidate_token_cache
    file_fn_ids = [
        fid for fid, fn in graph.functions.items() if fn.file == file_path
    ]
    invalidate_token_cache(file_fn_ids)

    # 3. Incremental intent for new/modified functions
    _generate_incremental_intents(graph, root, [file_path])

    # 4. Impact analysis
    diff = compute_diff(old_sigs, graph, [file_path])
    impact = format_impact(diff)

    # 4b. Value-domain change detection
    from winkers.value_locked import diff_collections
    value_changes = diff_collections(
        old_value_locked,
        graph.value_locked_collections,
        root=root,
    )

    # 5. Coherence check
    coherence = _coherence_check(file_path, root)

    # 6. Session state update
    session_store = SessionStore(root)
    session = session_store.load_or_create()

    event = WriteEvent(
        timestamp=datetime.now(UTC).isoformat(),
        file_path=file_path,
        functions_added=[fn.name for fn in diff.added],
        functions_modified=[sc.fn.name for sc in diff.signature_changed],
        functions_removed=diff.removed,
        signature_changes=[
            {"fn_id": sc.fn_id, "old_sig": sc.old_signature, "new_sig": sc.new_signature}
            for sc in diff.signature_changed
        ],
    )
    session.add_write(event)

    # Add warnings for broken callers
    for sc in diff.signature_changed:
        if sc.callers:
            session.add_warning(Warning(
                kind="broken_caller",
                severity="error" if len(sc.callers) > 0 else "warning",
                target=sc.fn_id,
                detail=(
                    f"{sc.fn.name}() signature changed: {sc.old_signature} → {sc.new_signature}. "
                    f"{len(sc.callers)} caller(s) may need updating."
                ),
            ))

    # Add warnings for coherence rules
    for rule in coherence:
        session.add_warning(Warning(
            kind="coherence",
            severity="warning",
            target=file_path,
            detail=f"Rule #{rule['id']} \"{rule['title']}\": check {', '.join(rule['sync_with'])}",
            fix_approach=rule.get("fix_approach", "sync"),
        ))

    # Add warnings for value-domain shrinkage
    for vc in value_changes:
        if not vc["removed"]:
            continue
        session.add_warning(Warning(
            kind="value_locked",
            severity="error" if vc["affected_literal_uses"] > 0 else "warning",
            target=f"{vc['file']}::{vc['name']}",
            detail=(
                f"{vc['name']}: removed {vc['removed']!r}; "
                f"{vc['affected_literal_uses']} caller literal use(s) at risk "
                f"in {len(vc['files_at_risk'])} file(s)."
            ),
        ))

    session_store.save(session)

    # Build response
    result: dict = {"file": file_path}

    if impact:
        result["impact"] = impact

    if value_changes:
        result["value_changes"] = value_changes

    if coherence:
        result["coherence"] = coherence

    result["session"] = session.summary()

    if session.pending_warnings():
        result["session"]["pending"] = [
            w.detail for w in session.pending_warnings()[:5]
        ]
        result["session"]["hint"] = "Call session_done() for an optional final audit."

    return result


def _coherence_check(file_path: str, root: Path) -> list[dict]:
    """Find coherence rules where 'affects' matches the modified file."""
    from winkers.conventions import RulesStore

    rules_file = RulesStore(root).load()
    matches: list[dict] = []

    for r in rules_file.rules:
        if r.category != "coherence":
            continue
        # Check if file_path matches any entry in r.affects
        if not any(file_path == a or file_path.endswith(a) for a in r.affects):
            continue

        entry: dict = {
            "id": r.id,
            "title": r.title,
            "content": r.content,
            "sync_with": r.sync_with,
            "fix_approach": r.fix_approach or "sync",
        }
        if r.wrong_approach:
            entry["wrong_approach"] = r.wrong_approach
        matches.append(entry)

    return matches


def _generate_incremental_intents(
    graph: Graph, root: Path, files: list[str],
) -> None:
    """Generate intents for new/modified functions (non-blocking).

    Only runs if intent provider was explicitly configured (not "auto").
    This prevents surprise API calls during impact_check.
    """
    try:
        from winkers.intent.provider import NoneProvider, auto_detect, load_config

        config = load_config(root)
        # Only generate if user explicitly chose a provider
        if config.provider in ("auto", "none"):
            return

        provider = auto_detect(config)
        if isinstance(provider, NoneProvider):
            return

        for file_path in files:
            file_node = graph.files.get(file_path)
            if not file_node:
                continue
            src_path = root / file_path
            if not src_path.exists():
                continue
            source = src_path.read_text(encoding="utf-8")

            for fn_id in file_node.function_ids:
                fn = graph.functions.get(fn_id)
                if fn is None or fn.intent:
                    continue
                intent = provider.generate(fn, source)
                if intent:
                    fn.intent = intent
    except Exception:
        pass  # Non-blocking: don't fail impact_check on intent errors
