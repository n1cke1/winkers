"""PostToolUse(Write|Edit) hook — impact check on file writes.

Runs graph update + impact analysis + coherence check automatically. Equivalent
to the `impact_check` MCP tool but triggered by Claude Code hooks protocol.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from winkers.hooks._debounce import file_hash, remember, should_skip
from winkers.hooks._logger import log_hook


def run(root: Path) -> None:
    """Read hook JSON from stdin, run impact_check logic, output context."""
    try:
        hook_data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    session_id = str(hook_data.get("session_id", ""))

    with log_hook(root, session_id, "PostToolUse", "post_write") as rec:
        tool_name = hook_data.get("tool_name", "")
        tool_input = hook_data.get("tool_input", {})
        rec["tool"] = tool_name

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
        rec["file"] = rel_path

        # Skip non-code files
        if not _is_code_file(rel_path):
            rec["outcome"] = "skip_non_code"
            sys.exit(0)

        # Content-hash debounce — same bytes processed earlier this session
        # → graph is already up to date, skip the full pipeline.
        # Catches idempotent reformat passes, MultiEdit no-op regions, git
        # checkout to a previously-cached state.
        current_hash = file_hash(Path(file_path))
        if current_hash is not None and should_skip(
            root, session_id, rel_path, current_hash
        ):
            rec["outcome"] = "skip_unchanged_content"
            rec["content_hash"] = current_hash[:12]
            sys.exit(0)

        from winkers.detection.impact import compute_diff, snapshot_signatures
        from winkers.session.state import SessionStore, Warning, WriteEvent
        from winkers.store import GraphStore
        from winkers.value_locked import diff_collections

        store = GraphStore(root)
        graph = store.load()
        if graph is None:
            rec["outcome"] = "no_graph"
            sys.exit(0)

        # 1. Snapshot old signatures + value_locked collections
        old_sigs = snapshot_signatures(graph, [rel_path])
        old_value_locked = [c.model_copy(deep=True) for c in graph.value_locked_collections]

        # 2. Incremental graph update (refreshes value_locked too)
        store.update_files(graph, [rel_path])
        store.save(graph)

        # 3. Impact analysis
        diff = compute_diff(old_sigs, graph, [rel_path])
        value_changes = diff_collections(
            old_value_locked,
            graph.value_locked_collections,
            root=root,
        )

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

        warnings_emitted = 0
        for sc in diff.signature_changed:
            if sc.callers:
                detail = (
                    f"{sc.fn.name}() signature changed: "
                    f"{sc.old_signature} → {sc.new_signature}. "
                    f"{len(sc.callers)} caller(s) may need updating."
                )
                session.add_warning(Warning(
                    kind="broken_caller",
                    severity="error",
                    target=sc.fn_id,
                    detail=detail,
                ))
                warnings_emitted += 1

        for rule in coherence:
            sync_list = ", ".join(rule["sync_with"])
            session.add_warning(Warning(
                kind="coherence",
                severity="warning",
                target=rel_path,
                detail=f"Rule #{rule['id']} \"{rule['title']}\": check {sync_list}",
                fix_approach=rule.get("fix_approach", "sync"),
            ))
            warnings_emitted += 1

        for vc in value_changes:
            if not vc["removed"]:
                continue
            literal_hits = vc.get("string_literal_hits") or {}
            literal_total = literal_hits.get("total", 0)
            literal_files = len(literal_hits.get("files", []))
            # Severity escalates if EITHER call-site literal-use OR a
            # repo-wide string-literal scan finds risk. The scan covers
            # bare comparisons / fixtures / SQL / templates that the
            # call-graph metric is structurally blind to (I5 trap).
            severity = (
                "error"
                if vc["affected_literal_uses"] > 0 or literal_total > 0
                else "warning"
            )
            detail = (
                f"{vc['name']}: removed {vc['removed']!r}; "
                f"{vc['affected_literal_uses']} call-site literal use(s) "
                f"in same-file consumers"
            )
            if literal_total:
                detail += (
                    f" + {literal_total} string-literal occurrence(s) "
                    f"in {literal_files} file(s) repo-wide"
                )
            detail += " at risk."
            session.add_warning(Warning(
                kind="value_locked",
                severity=severity,
                target=f"{vc['file']}::{vc['name']}",
                detail=detail,
            ))
            warnings_emitted += 1

        session_store.save(session)

        # Cache the post-update content hash so a subsequent Edit with
        # identical bytes short-circuits at the top of the next run.
        if current_hash is not None:
            remember(root, session_id, rel_path, current_hash)

        rec["fns_added"] = len(diff.added)
        rec["fns_modified"] = len(diff.signature_changed)
        rec["fns_removed"] = len(diff.removed)
        rec["coherence_rules"] = len(coherence)
        rec["value_changes"] = len(value_changes)
        rec["warnings_emitted"] = warnings_emitted

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
            literal_hits = vc.get("string_literal_hits") or {}
            literal_total = literal_hits.get("total", 0)
            literal_files = len(literal_hits.get("files", []))
            lines.append(
                f"  VALUE_LOCKED: {vc['name']} removed {vc['removed']} — "
                f"{vc['affected_literal_uses']} call-site literal use(s)"
                " in same-file consumers"
            )
            if literal_total:
                lines.append(
                    f"    + {literal_total} string-literal occurrence(s)"
                    f" in {literal_files} file(s) repo-wide:"
                )
                # Show top-3 hits across all values for a quick sanity
                # check; full list is in vc["string_literal_hits"].
                shown = 0
                by_value = literal_hits.get("by_value", {})
                for value, items in by_value.items():
                    for item in items:
                        if shown >= 3:
                            break
                        lines.append(
                            f"      {item['file']}:{item['line']}"
                            f"  {value!r} → {item['snippet']}"
                        )
                        shown += 1
                    if shown >= 3:
                        break

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
