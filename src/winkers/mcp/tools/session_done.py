"""MCP tool: session_done — final-audit verdict (PASS / WARN / FAIL) over session writes."""

from __future__ import annotations

from pathlib import Path

from winkers.models import Graph


def _tool_session_done(graph: Graph, root: Path) -> dict:
    """Session audit — Wave 6 three-tier verdict (PASS / WARN / FAIL).

    Criteria (CONCEPT.md §5):

    FAIL — high precision, structural breakage:
      - Unresolved `broken_caller` warnings (signature changed but callers
        not updated).
      - `coherence` rule with `fix_approach=sync` whose sync_with files
        were not touched.
      - Complexity-delta regression beyond budget.

    WARN — soft signals that don't block:
      - Writes happened but no `before_create` was registered for the
        session (terra incognita choice — surface but don't fail).
      - `value_locked` warnings still present (literal_hits surfaced
        by post_write but neither resolved nor blocking).
      - `coherence` rules with `fix_approach=derived|refactor`.

    PASS — none of the above.

    Anti-loop: on the second+ call we still report the same status —
    Wave 6 dropped the prior "always PASS on repeat" behaviour because
    the Stop hook no longer forces continuation; agents calling
    session_done() repeatedly just get the current verdict.
    """
    from winkers.session.state import SessionStore

    session_store = SessionStore(root)
    session = session_store.load_or_create()

    session.session_done_calls += 1

    issues: list[dict] = []        # FAIL-level
    warnings_list: list[dict] = []  # WARN-level
    recommendations: list[dict] = []

    # FAIL — broken callers
    for w in session.pending_warnings():
        if w.kind == "broken_caller":
            callers_info = _broken_caller_details(w.target, graph)
            issues.append({
                "kind": "broken_caller",
                "detail": w.detail,
                "call_sites": callers_info,
            })

    # FAIL / recommendation — coherence sync_with vs derived
    modified_files = set(session.files_modified())
    for w in session.pending_warnings():
        if w.kind != "coherence":
            continue
        if w.fix_approach == "sync":
            sync_files = _extract_sync_files(w, root)
            unmodified = [f for f in sync_files if f not in modified_files]
            if unmodified:
                issues.append({
                    "kind": "coherence_sync",
                    "detail": w.detail,
                    "unmodified_files": unmodified,
                })
        else:
            recommendations.append({
                "kind": f"coherence_{w.fix_approach or 'derived'}",
                "detail": w.detail,
            })

    # FAIL — complexity-delta regression
    cx_issue = _check_complexity_delta(graph, session)
    if cx_issue:
        issues.append(cx_issue)

    # WARN — value_locked still pending
    for w in session.pending_warnings():
        if w.kind == "value_locked":
            warnings_list.append({
                "kind": "value_locked",
                "severity": w.severity,
                "detail": w.detail,
            })

    # WARN — writes happened but no before_create registered
    if (
        len(session.writes) > 0
        and session.before_create_calls == 0
    ):
        warnings_list.append({
            "kind": "no_intent_registered",
            "detail": (
                f"{len(session.writes)} write(s) without a single"
                " before_create call — terra incognita work, no audit"
                " axis to verify intent fulfillment."
            ),
        })

    session_store.save(session)

    if issues:
        status = "FAIL"
    elif warnings_list:
        status = "WARN"
    else:
        status = "PASS"

    result: dict = {
        "status": status,
        "session": session.summary(),
    }
    if issues:
        result["issues"] = issues
        result["hint"] = (
            "Resolve the issues above. Stop hook will not block; the"
            " status lands in audit.json and the next session's"
            " prompt enrichment will surface it."
        )
    if warnings_list:
        result["warnings"] = warnings_list
    if recommendations:
        result["recommendations"] = recommendations
    return result


def _broken_caller_details(fn_id: str, graph: Graph) -> list[dict]:
    """Get call site details for a broken caller warning."""
    callers = graph.callers(fn_id)
    return [
        {
            "fn": e.source_fn,
            "file": e.call_site.file,
            "line": e.call_site.line,
            "expression": e.call_site.expression,
        }
        for e in callers
    ]


def _extract_sync_files(warning, root: Path) -> list[str]:
    """Extract sync_with file list from a coherence warning."""
    from winkers.conventions import RulesStore

    rules_file = RulesStore(root).load()
    # Match by rule id in the warning detail (e.g. "Rule #14")
    import re
    match = re.search(r"Rule #(\d+)", warning.detail)
    if match:
        rule_id = int(match.group(1))
        for r in rules_file.rules:
            if r.id == rule_id:
                return r.sync_with
    return []


def _check_complexity_delta(graph: Graph, session) -> dict | None:
    """Check if total complexity grew too much during this session."""
    if not session.graph_snapshot_at_start:
        return None

    # Compare complexity of modified files
    modified_files = set(session.files_modified())
    if not modified_files:
        return None

    # Sum current complexity of modified files
    modified_fns = [
        fn for fn in graph.functions.values()
        if fn.file in modified_files
    ]
    if not modified_fns:
        return None

    new_cx = sum(fn.complexity or 0 for fn in modified_fns)

    # Flag if average complexity is very high per function
    avg_cx = new_cx / len(modified_fns)
    if avg_cx > 15:
        return {
            "kind": "debt_regression",
            "detail": (
                f"Average complexity in modified files is {avg_cx:.0f} "
                f"(threshold: 15). Consider simplifying."
            ),
        }
    return None
