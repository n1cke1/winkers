"""Impact analysis — signature changes and caller impact."""

from __future__ import annotations

from dataclasses import dataclass, field

from winkers.models import CallEdge, FunctionNode, Graph

# ---------------------------------------------------------------------------
# Signature representation
# ---------------------------------------------------------------------------

def _signature_key(fn: FunctionNode) -> str:
    """Canonical signature string for comparison."""
    params = ", ".join(
        f"{p.name}:{p.type_hint or '?'}" for p in fn.params
    )
    ret = fn.return_type or "?"
    return f"({params}) -> {ret}"


# ---------------------------------------------------------------------------
# Diff between old and new graph state
# ---------------------------------------------------------------------------

@dataclass
class FunctionDiff:
    added: list[FunctionNode] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # fn_ids
    signature_changed: list[SignatureChange] = field(default_factory=list)


@dataclass
class SignatureChange:
    fn_id: str
    fn: FunctionNode
    old_signature: str
    new_signature: str
    callers: list[CallEdge] = field(default_factory=list)


def compute_diff(
    old_functions: dict[str, str],  # {fn_id: signature_key}
    graph: Graph,
    changed_files: list[str],
) -> FunctionDiff:
    """Compare old function signatures with current graph for changed files.

    Args:
        old_functions: Snapshot of {fn_id: signature_key} before update
        graph: Current graph (after update_files)
        changed_files: Files that were modified
    """
    diff = FunctionDiff()

    # Current functions in changed files
    current_fn_ids: set[str] = set()
    for file_path in changed_files:
        file_node = graph.files.get(file_path)
        if file_node:
            current_fn_ids.update(file_node.function_ids)
        else:
            # Fallback: scan functions by file attribute
            for fn_id, fn in graph.functions.items():
                if fn.file == file_path:
                    current_fn_ids.add(fn_id)

    # Old functions in changed files
    old_fn_ids = {fid for fid in old_functions if _fn_in_files(fid, changed_files)}

    # Added functions
    for fn_id in current_fn_ids - old_fn_ids:
        fn = graph.functions.get(fn_id)
        if fn:
            diff.added.append(fn)

    # Removed functions
    for fn_id in old_fn_ids - current_fn_ids:
        diff.removed.append(fn_id)

    # Signature changes
    for fn_id in current_fn_ids & old_fn_ids:
        fn = graph.functions.get(fn_id)
        if fn is None:
            continue
        new_sig = _signature_key(fn)
        old_sig = old_functions.get(fn_id, "")
        if old_sig and new_sig != old_sig:
            callers = graph.callers(fn_id)
            diff.signature_changed.append(SignatureChange(
                fn_id=fn_id,
                fn=fn,
                old_signature=old_sig,
                new_signature=new_sig,
                callers=callers,
            ))

    return diff


def _fn_in_files(fn_id: str, files: list[str]) -> bool:
    """Check if a function ID belongs to one of the given files."""
    # fn_id format: "path/to/file.py::function_name"
    file_part = fn_id.split("::")[0]
    return file_part in files


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def snapshot_signatures(graph: Graph, files: list[str] | None = None) -> dict[str, str]:
    """Take a snapshot of {fn_id: signature_key} for given files (or all)."""
    result: dict[str, str] = {}
    for fn_id, fn in graph.functions.items():
        if files is None or fn.file in files:
            result[fn_id] = _signature_key(fn)
    return result


# ---------------------------------------------------------------------------
# Format impact for MCP response
# ---------------------------------------------------------------------------

def format_impact(diff: FunctionDiff) -> dict:
    """Format FunctionDiff as a dict for MCP tool response."""
    result: dict = {}

    if diff.added:
        result["added"] = [
            {"function": fn.name, "file": fn.file, "line": fn.line_start}
            for fn in diff.added
        ]

    if diff.removed:
        result["removed"] = diff.removed

    if diff.signature_changed:
        result["signature_changes"] = []
        for sc in diff.signature_changed:
            entry: dict = {
                "function": sc.fn.name,
                "file": sc.fn.file,
                "old_signature": sc.old_signature,
                "new_signature": sc.new_signature,
            }
            if sc.callers:
                has_default_only = _is_additive_change(sc.old_signature, sc.new_signature)
                entry["callers_count"] = len(sc.callers)
                entry["callers"] = [
                    {
                        "fn": e.source_fn,
                        "file": e.call_site.file,
                        "line": e.call_site.line,
                    }
                    for e in sc.callers
                ]
                if has_default_only:
                    entry["note"] = (
                        "New param has default — callers won't break, "
                        "but review if they should pass actual value."
                    )
                else:
                    entry["warning"] = "Breaking change — callers must be updated."
            result["signature_changes"].append(entry)

    return result


def _is_additive_change(old_sig: str, new_sig: str) -> bool:
    """Heuristic: check if change is just adding params (non-breaking)."""
    # If old params are a prefix of new params, likely additive
    return new_sig.startswith(old_sig.split(")")[0])
