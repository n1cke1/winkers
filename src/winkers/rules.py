"""Graph rules — violation detection for analyze."""

from __future__ import annotations

from dataclasses import dataclass, field

from winkers.models import Graph


@dataclass
class Violation:
    rule: str
    severity: str  # "error" | "warning" | "info"
    function: str | None = None
    file: str | None = None
    detail: str = ""
    affected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "function": self.function,
            "file": self.file,
            "detail": self.detail,
            "affected": self.affected,
        }


def check_violations(
    old_graph: Graph,
    new_graph: Graph,
    config: dict | None = None,
) -> list[Violation]:
    """Run all rules against old→new graph transition."""
    violations: list[Violation] = []
    cfg = config or {}

    violations.extend(_check_signature_changed(old_graph, new_graph))
    violations.extend(_check_cross_zone_import(new_graph, cfg))
    violations.extend(_check_circular_dependency(new_graph))
    violations.extend(_check_orphan_export(new_graph))

    return violations


# ---------------------------------------------------------------------------
# Rule 1: signature_changed
# ---------------------------------------------------------------------------

def _signature(fn) -> str:
    params = ", ".join(
        f"{p.name}: {p.type_hint}" if p.type_hint else p.name
        for p in fn.params
    )
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    return f"({params}){ret}"


def _check_signature_changed(old_graph: Graph, new_graph: Graph) -> list[Violation]:
    violations: list[Violation] = []
    for fn_id, old_fn in old_graph.functions.items():
        new_fn = new_graph.functions.get(fn_id)
        old_locked = old_graph.is_locked(fn_id)

        if new_fn is None:
            if old_locked:
                callers = [e.source_fn for e in old_graph.callers(fn_id)]
                violations.append(Violation(
                    rule="function_removed",
                    severity="error",
                    function=fn_id,
                    detail=f"locked function removed; {len(callers)} caller(s) affected",
                    affected=callers,
                ))
        elif old_locked and _signature(old_fn) != _signature(new_fn):
            callers = [e.source_fn for e in new_graph.callers(fn_id)]
            violations.append(Violation(
                rule="signature_changed",
                severity="error",
                function=fn_id,
                detail=(
                    f"signature changed: {_signature(old_fn)}"
                    f" → {_signature(new_fn)}"
                ),
                affected=callers,
            ))
    return violations


# ---------------------------------------------------------------------------
# Rule 2: cross_zone_import
# ---------------------------------------------------------------------------

def _infer_zone(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else "root"


def _check_cross_zone_import(graph: Graph, config: dict) -> list[Violation]:
    zone_rules: dict[str, list[str]] = config.get("zones", {})
    if not zone_rules:
        return []

    violations: list[Violation] = []
    for edge in graph.import_edges:
        src_zone = _infer_zone(edge.source_file)
        tgt_zone = _infer_zone(edge.target_file)
        if src_zone == tgt_zone:
            continue
        allowed = zone_rules.get(src_zone, {})
        if isinstance(allowed, dict):
            allowed = allowed.get("can_import", [])
        if tgt_zone not in allowed:
            violations.append(Violation(
                rule="cross_zone_import",
                severity="warning",
                file=edge.source_file,
                detail=(
                    f"zone '{src_zone}' imports from '{tgt_zone}'"
                    f" (not in can_import list)"
                ),
                affected=[edge.target_file],
            ))
    return violations


# ---------------------------------------------------------------------------
# Rule 3: circular_dependency
# ---------------------------------------------------------------------------

def _check_circular_dependency(graph: Graph) -> list[Violation]:
    # Build adjacency: file → set of imported files
    adj: dict[str, set[str]] = {}
    for edge in graph.import_edges:
        adj.setdefault(edge.source_file, set()).add(edge.target_file)

    violations: list[Violation] = []
    seen_cycles: set[frozenset[str]] = set()

    def dfs(node: str, path: list[str], visited: set[str]) -> None:
        if node in visited:
            # Found cycle
            cycle_start = path.index(node)
            cycle = path[cycle_start:]
            key = frozenset(cycle)
            if key not in seen_cycles:
                seen_cycles.add(key)
                violations.append(Violation(
                    rule="circular_dependency",
                    severity="warning",
                    detail=f"circular import: {' → '.join(cycle + [node])}",
                    affected=cycle,
                ))
            return
        visited.add(node)
        path.append(node)
        for neighbor in adj.get(node, set()):
            dfs(neighbor, path, visited)
        path.pop()
        visited.discard(node)

    for start in adj:
        dfs(start, [], set())

    return violations


# ---------------------------------------------------------------------------
# Rule 4: orphan_export
# ---------------------------------------------------------------------------

def _check_orphan_export(graph: Graph) -> list[Violation]:
    violations: list[Violation] = []
    for fn_id, fn in graph.functions.items():
        if fn.is_exported and not graph.is_locked(fn_id):
            violations.append(Violation(
                rule="orphan_export",
                severity="info",
                function=fn_id,
                detail=(
                    f"exported function '{fn.name}' has no callers"
                    " — possible dead code or entry point"
                ),
            ))
    return violations
