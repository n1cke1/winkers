"""Technical debt analysis from the dependency graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from winkers.models import Graph


@dataclass
class DebtItem:
    category: str       # complexity | long_function | circular_import | monster_file | orphan
    severity: str       # "high" | "medium" | "low"
    target: str         # function id, file path, or zone
    detail: str         # human-readable explanation
    value: int = 0      # numeric metric (complexity, lines, etc.)


@dataclass
class DebtReport:
    items: list[DebtItem] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "items": [
                {
                    "category": i.category,
                    "severity": i.severity,
                    "target": i.target,
                    "detail": i.detail,
                    "value": i.value,
                }
                for i in self.items
            ],
        }


def compute_debt(graph: Graph) -> DebtReport:
    """Analyze graph and return technical debt report."""
    report = DebtReport()

    _check_complexity(graph, report)
    _check_long_functions(graph, report)
    _check_monster_files(graph, report)
    _check_circular_imports(graph, report)
    _check_orphan_exports(graph, report)

    # Summary counts
    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_category: dict[str, int] = {}
    for item in report.items:
        by_severity[item.severity] = by_severity.get(item.severity, 0) + 1
        by_category[item.category] = by_category.get(item.category, 0) + 1

    score = by_severity.get("high", 0) * 3 + by_severity.get("medium", 0)
    fn_count = len(graph.functions) or 1
    density = round(score / fn_count * 100, 1)

    report.summary = {
        "total_issues": len(report.items),
        "by_severity": by_severity,
        "by_category": by_category,
        "score": score,
        "density": density,  # debt points per 100 functions
    }
    return report


def _check_complexity(graph: Graph, report: DebtReport) -> None:
    """Flag functions with high cyclomatic complexity."""
    for fn in graph.functions.values():
        if fn.complexity >= 15:
            report.items.append(DebtItem(
                category="complexity",
                severity="high",
                target=fn.id,
                detail=f"Cyclomatic complexity {fn.complexity} (threshold: 15)",
                value=fn.complexity,
            ))
        elif fn.complexity >= 10:
            report.items.append(DebtItem(
                category="complexity",
                severity="medium",
                target=fn.id,
                detail=f"Cyclomatic complexity {fn.complexity} (threshold: 10)",
                value=fn.complexity,
            ))


def _check_long_functions(graph: Graph, report: DebtReport) -> None:
    """Flag functions exceeding line thresholds."""
    for fn in graph.functions.values():
        if fn.lines >= 100:
            report.items.append(DebtItem(
                category="long_function",
                severity="high",
                target=fn.id,
                detail=f"{fn.lines} lines (threshold: 100)",
                value=fn.lines,
            ))
        elif fn.lines >= 50:
            report.items.append(DebtItem(
                category="long_function",
                severity="medium",
                target=fn.id,
                detail=f"{fn.lines} lines (threshold: 50)",
                value=fn.lines,
            ))


def _check_monster_files(graph: Graph, report: DebtReport) -> None:
    """Flag files with too many functions."""
    for path, file_node in graph.files.items():
        count = len(file_node.function_ids)
        if count >= 30:
            report.items.append(DebtItem(
                category="monster_file",
                severity="high",
                target=path,
                detail=f"{count} functions (threshold: 30)",
                value=count,
            ))
        elif count >= 15:
            report.items.append(DebtItem(
                category="monster_file",
                severity="medium",
                target=path,
                detail=f"{count} functions (threshold: 15)",
                value=count,
            ))


def _check_circular_imports(graph: Graph, report: DebtReport) -> None:
    """Detect import cycles between files."""
    # Build adjacency: file → set of imported files
    adj: dict[str, set[str]] = {}
    for edge in graph.import_edges:
        adj.setdefault(edge.source_file, set()).add(edge.target_file)

    # Find mutual imports (A→B and B→A)
    seen: set[tuple[str, str]] = set()
    for a, targets in adj.items():
        for b in targets:
            if b in adj and a in adj[b]:
                pair = tuple(sorted([a, b]))
                if pair not in seen:
                    seen.add(pair)
                    report.items.append(DebtItem(
                        category="circular_import",
                        severity="medium",
                        target=f"{pair[0]} <-> {pair[1]}",
                        detail=f"Mutual import between {pair[0]} and {pair[1]}",
                    ))


def _check_orphan_exports(graph: Graph, report: DebtReport) -> None:
    """Flag exported functions with zero callers."""
    for fn in graph.functions.values():
        if fn.is_exported and not graph.is_locked(fn.id):
            report.items.append(DebtItem(
                category="orphan",
                severity="low",
                target=fn.id,
                detail="Exported but never called within the project",
            ))
