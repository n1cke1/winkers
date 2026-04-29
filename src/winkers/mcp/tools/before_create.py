"""MCP tool: before_create — pre-write context for a creation/change intent."""

from __future__ import annotations

from pathlib import Path

from winkers.mcp.tools._common import _attach_route, _load_impact
from winkers.models import Graph

_AFFECTED_FNS_LIMIT = 5  # cap for zone-expanded function lists

# Keywords that suggest value-domain shrinking (vs additive change). Matched
# stem-wise via the same stemmer search.py uses.
_VALUE_REMOVAL_KEYWORDS = frozenset({
    "simplify", "reduce", "remove", "delete", "consolidate", "shrink",
    "drop", "prune", "collapse", "merge",
})


def _tool_before_create(graph: Graph, args: dict, root: Path) -> dict:
    from winkers.search import format_before_create_response, search_functions
    from winkers.target_resolution import categorize_intent, resolve_targets

    intent = args.get("intent", "")
    zone = args.get("zone", "")

    if not intent:
        return {"error": "Provide 'intent' — what you want to create or change."}

    category = categorize_intent(intent)
    targets = resolve_targets(intent, graph)

    if category == "change":
        if targets.functions or targets.paths:
            return _before_create_change(
                graph, intent, targets, explicit_fns=targets.functions, root=root,
            )
        # No explicit targets — fall back to FTS5 to derive function targets.
        fallback_matches = search_functions(graph, intent, zone=zone)
        if fallback_matches:
            from winkers.target_resolution import ResolvedTargets

            derived = ResolvedTargets()
            for m in fallback_matches:
                if m.fn.id not in derived.functions:
                    derived.functions.append(m.fn.id)
            return _before_create_change(
                graph, intent, derived, explicit_fns=derived.functions, root=root,
            )
        return _before_create_unknown(graph, intent, root)

    if category == "unknown" and targets.is_empty():
        # No keywords, no named targets — give architectural context.
        fallback_matches = search_functions(graph, intent, zone=zone)
        if not fallback_matches:
            return _before_create_unknown(graph, intent, root)
        response = format_before_create_response(
            graph, intent, fallback_matches, zone=zone, root=root,
        )
        response["intent_type"] = "create"
        return response

    # Default: create / fallback.
    matches = search_functions(graph, intent, zone=zone)
    response = format_before_create_response(graph, intent, matches, zone=zone, root=root)
    response["intent_type"] = "create"
    return response


def _before_create_change(
    graph: Graph,
    intent: str,
    targets,
    explicit_fns: list[str],
    root: Path | None = None,
) -> dict:
    """Adaptive response for `change` intents.

    `explicit_fns` are function ids the user named directly in the intent
    (or FTS5-derived from the intent). They are always shown in full.
    Functions discovered by zone/file expansion are added on top, but only
    if locked, and capped at _AFFECTED_FNS_LIMIT.
    """
    file_paths = sorted(set(targets.paths))
    explicit_set = set(explicit_fns)

    # Files derived from explicit functions — used only for the `files` block
    # (import/migration metrics). Not surfaced in resolved_targets (user didn't
    # name them) and not fed into zone expansion (would inflate `functions`).
    derived_files = {
        graph.functions[fid].file
        for fid in explicit_fns
        if fid in graph.functions
    }
    analysis_paths = sorted(set(file_paths) | derived_files)

    response: dict = {
        "intent_type": "change",
        "intent": intent,
        "resolved_targets": {
            "files": file_paths,
            "functions": [f for f in explicit_fns if f in graph.functions],
        },
    }

    if analysis_paths:
        response["files"] = _files_block(
            graph, analysis_paths, explicit_fns=explicit_fns,
        )

    fn_block = _functions_block(graph, file_paths, explicit_set, root=root)
    if fn_block is not None:
        response["functions"] = fn_block

    value_block = _value_changes_block(graph, intent, file_paths, explicit_fns)
    if value_block:
        response["value_changes"] = value_block

    duplication_block = _duplication_warning(graph, explicit_fns)
    if duplication_block:
        response["similar_logic"] = duplication_block

    return response


def _duplication_warning(graph: Graph, explicit_fns: list[str]) -> list[dict]:
    """If an explicit target function shares secondary_intents with others,
    surface them so the caller doesn't inadvertently extend duplicated logic."""
    seen_tags: set[str] = set()
    out: list[dict] = []
    for fid in explicit_fns:
        fn = graph.functions.get(fid)
        if fn is None or not fn.secondary_intents:
            continue
        for tag in fn.secondary_intents:
            if tag in seen_tags:
                continue
            others = [
                other.id for other in graph.functions.values()
                if other.id != fid and tag in (other.secondary_intents or [])
            ]
            if not others:
                continue
            seen_tags.add(tag)
            out.append({
                "intent": tag,
                "source": fid,
                "also_in": others[:10],
                "suggestion": f"'{tag}' appears in other functions too — "
                "consider extracting instead of duplicating.",
            })
    return out


def _value_changes_block(
    graph: Graph,
    intent: str,
    file_paths: list[str],
    explicit_fns: list[str],
) -> dict | None:
    """If intent looks like a value-domain shrink AND it touches a file (or
    referencing function) with a value_locked collection, surface a warning
    block per affected collection.
    """
    if not graph.value_locked_collections:
        return None

    # Only fire on shrink-style intents — additive changes don't break callers.
    import re as _re

    from winkers.search import stem
    intent_stems = {stem(w.lower()) for w in _re.findall(r"[A-Za-z][A-Za-z0-9]*", intent)}
    removal_stems = {stem(w) for w in _VALUE_REMOVAL_KEYWORDS}
    if not (intent_stems & removal_stems):
        return None

    file_set = set(file_paths)
    explicit_set = set(explicit_fns)

    block: dict = {}
    for c in graph.value_locked_collections:
        # Match if collection's file is targeted, or any explicit fn references it.
        relevant = (
            c.file in file_set
            or any(fid in explicit_set for fid in c.referenced_by_fns)
        )
        if not relevant:
            continue
        total_uses = sum(c.literal_uses.values())
        block[c.name] = {
            "value_locked": True,
            "file": c.file,
            "values": list(c.values),
            "total_literal_uses": total_uses,
            "files_at_risk": len(c.files_with_uses),
        }
    return block or None


def _files_block(
    graph: Graph,
    file_paths: list[str],
    explicit_fns: set[str] | None = None,
) -> dict:
    """Per-file coupling and impact metrics for the before_create `files` block.

    Three distinct "how much needs to change" signals:

    - ``importing_files``: unique external files that import from anything in
      the resolved set. Upper bound on "files that reference our module
      surface" — a module-level change touches all of them.
    - ``migration_cost``: raw count of *import edges* from external files.
      Will exceed ``len(importing_files)`` when a single file imports several
      symbols from a target. Kept for backward compat; prefer
      ``direct_caller_files`` for fn-level intents.
    - ``direct_caller_files``: when the intent names specific functions
      (``explicit_fns``), the unique external files that actually *invoke*
      those functions. This is the real hands-on editing surface — only set
      when explicit fns are resolved, otherwise the importer-level bound is
      as tight as we can be without fn info.
    """
    from winkers.target_resolution import is_test_path

    resolved_set = set(file_paths)
    explicit_fns = explicit_fns or set()

    cross_imports = 0
    external_importers: set[str] = set()
    migration_cost = 0
    prod_locked = 0
    test_locked = 0

    for path in file_paths:
        for edge in graph.imports_from_file(path):
            if edge.target_file in resolved_set and edge.target_file != path:
                cross_imports += 1
        for edge in graph.imported_by_file(path):
            if edge.source_file in resolved_set:
                continue
            external_importers.add(edge.source_file)
            migration_cost += 1
        fnode = graph.files.get(path)
        if not fnode:
            continue
        path_is_test = is_test_path(path)
        for fid in fnode.function_ids:
            if not graph.is_locked(fid):
                continue
            if path_is_test:
                test_locked += 1
                continue
            # Prod-file fn: only count as "locked" if it has at least one
            # production caller. Pytest-fixture-like references from tests
            # shouldn't inflate the number.
            if any(not is_test_path(e.call_site.file) for e in graph.callers(fid)):
                prod_locked += 1

    block: dict = {
        "cross_imports": cross_imports,
        "imported_by": sorted(external_importers),
        "importing_files": len(external_importers),
        "migration_cost": migration_cost,
        "locked_fns": prod_locked,
    }
    if test_locked:
        block["locked_test_fns"] = test_locked

    # Direct-caller surface: only meaningful when the intent named specific
    # functions. Collect external files that contain actual call-sites to
    # those functions, not just an import of the module.
    if explicit_fns:
        direct_caller_files: set[str] = set()
        for fid in explicit_fns:
            if fid not in graph.functions:
                continue
            for edge in graph.callers(fid):
                caller_file = edge.call_site.file
                if caller_file in resolved_set:
                    continue
                direct_caller_files.add(caller_file)
        block["direct_caller_files"] = sorted(direct_caller_files)
        block["direct_caller_files_count"] = len(direct_caller_files)

    return block


def _functions_block(
    graph: Graph,
    file_paths: list[str],
    explicit_fns: set[str],
    root: Path | None = None,
) -> dict | None:
    """Build affected_fns list. Explicit fns shown in full; zone-expanded
    fns shown locked-only and capped, with totals so the agent sees scale."""

    # Functions discovered by zone/file expansion (not user-named).
    expanded_locked: list[str] = []
    expanded_total_locked = 0
    expanded_total_callers = 0
    for path in file_paths:
        fnode = graph.files.get(path)
        if not fnode:
            continue
        for fid in fnode.function_ids:
            if fid in explicit_fns:
                continue
            if not graph.is_locked(fid):
                continue
            expanded_total_locked += 1
            expanded_total_callers += len(graph.callers(fid))
            expanded_locked.append(fid)

    # Sort expansion by callers desc so the cap keeps the most-impactful ones.
    expanded_locked.sort(key=lambda fid: len(graph.callers(fid)), reverse=True)
    shown_expanded = expanded_locked[:_AFFECTED_FNS_LIMIT]

    chosen_ids = list(explicit_fns) + [
        fid for fid in shown_expanded if fid not in explicit_fns
    ]
    if not chosen_ids:
        return None

    impact = _load_impact(root) if root is not None else None

    affected: list[dict] = []
    for fid in chosen_ids:
        fn = graph.functions.get(fid)
        if fn is None:
            continue
        caller_edges = graph.callers(fid)
        test_count = sum(
            1 for e in caller_edges
            if "test" in e.call_site.file.lower()
        )
        affected_entry: dict = {
            "name": fn.name,
            "file": fn.file,
            "locked": bool(caller_edges),
            "callers_count": len(caller_edges),
            "test_callers_count": test_count,
            "callers": [
                {
                    "file": e.call_site.file,
                    "line": e.call_site.line,
                    "fn": e.source_fn,
                    "expression": e.call_site.expression,
                }
                for e in caller_edges[:8]
            ],
        }
        if fn.intent:
            affected_entry["intent"] = fn.intent
        _attach_route(affected_entry, fn)
        if impact is not None:
            report = impact.functions.get(fid)
            if report is not None:
                affected_entry["risk_level"] = report.risk_level
                if report.dangerous_operations:
                    affected_entry["dangerous_operations"] = list(report.dangerous_operations)
        affected.append(affected_entry)

    block: dict = {"affected_fns": affected}

    # Truncation counters: only when zone-expansion actually skipped something.
    if expanded_total_locked > len(shown_expanded):
        block["total_locked"] = expanded_total_locked + len(explicit_fns)
        block["total_callers"] = expanded_total_callers + sum(
            len(graph.callers(fid)) for fid in explicit_fns if fid in graph.functions
        )
        block["shown"] = len(affected)

    return block


def _before_create_unknown(graph: Graph, intent: str, root: Path) -> dict:
    """Compact, actionable response when intent can't be resolved.

    Returning architectural context here (hotspots + zone_intents) produced
    ~1.5k tokens of noise that agents took as relevant — strictly worse
    than an empty answer. A short "rewrite your intent like X" is more
    useful and a lot cheaper.
    """
    return {
        "intent_type": "unknown",
        "intent": intent,
        "error": "Could not parse intent into resolvable targets.",
        "hint": (
            "Rewrite the intent with explicit markers:"
            " `fn_name()` for functions (e.g. calculate_price()),"
            " `Class.method()` for methods,"
            " `Class.attribute` (no parens) for SQLAlchemy relationships,"
            " Pydantic / dataclass fields, or other attribute-level targets,"
            " or a relative file path (e.g. app/repos/invoice.py)."
            " Combine for precision: 'refactor calculate_price() in"
            " app/pricing.py'. Multiple attrs in one go are fine:"
            " 'fix Client.invoices, Client.payments, Client.contracts'."
        ),
        "examples": [
            "refactor calculate_price() to round half-even",
            "fix InvoiceRepo.get_with_items() selectinload",
            "fix Client.invoices, Client.payments cascade",
            "add soft_delete() to app/repos/base.py",
            "rename modules/pricing.py::calc_tax to apply_tax",
        ],
        "fallback": (
            "For architectural context call"
            " orient(task='<your task>', include=['map','conventions','hotspots'])."
        ),
    }
