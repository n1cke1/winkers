"""value_locked detector — collections of literal values + caller literal-arg usage.

Catches the value-domain breaking change pattern: a module-level collection
(`VALID_STATUSES = {"draft", "sent", ...}`) is consumed by a function
(`def can_transition(status: str): return status in VALID_STATUSES`), and
callers pass literals from that collection (`can_transition("draft")`).
Removing a value silently breaks every caller that used it as a literal —
the function signature stays `(str) -> bool`, so the existing `locked`
marker doesn't fire.

Detection is AST-only (tree-sitter), Python-only in this MVP. Bounded:
collections of up to 64 literal str/int/float values.
"""

from __future__ import annotations

import re
from pathlib import Path

from winkers.languages.python import PythonProfile
from winkers.models import Graph, ValueLockedCollection
from winkers.parser import ParseResult, TreeSitterParser

_MAX_VALUES = 64

# Module-level `NAME = {...}` or `NAME = frozenset({...})`.
# (call) wrapper is matched with optional set inside argument_list to catch
# `frozenset({"a","b"})` and `set({"a","b"})`. Bare `NAME = {...}` matches
# the `set` rule directly.
_COLLECTION_QUERY = """
(module
  (expression_statement
    (assignment
      left: (identifier) @collection.name
      right: [
        (set) @collection.body
        (call
          function: (identifier) @collection.ctor
          arguments: (argument_list (set) @collection.body))
      ])))
"""

# Quoted string literal extraction (single OR double quotes, no escapes).
_STR_LITERAL_RE = re.compile(r"""(?:"([^"\\]*)"|'([^'\\]*)')""")
_INT_LITERAL_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?![A-Za-z0-9_.])")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_value_locked(graph: Graph, root: Path) -> None:
    """Populate ``graph.value_locked_collections`` in place.

    Idempotent — clears the existing list first.
    """
    detector = _Detector()
    detector.run(graph, root)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

class _Detector:
    def __init__(self) -> None:
        self._parser = TreeSitterParser()
        self._profile = PythonProfile()

    def run(self, graph: Graph, root: Path) -> None:
        graph.value_locked_collections = []

        # Pass 1 — find collections + which functions reference each name.
        for rel in graph.files:
            if not rel.endswith(".py"):
                continue
            path = root / rel
            if not path.is_file():
                continue
            try:
                source = path.read_bytes()
            except OSError:
                continue
            try:
                pr = self._parser.parse_source(source, self._profile)
            except Exception:
                continue
            self._scan_file(graph, rel, source, pr)

        # Pass 2 — count literal usages in callers.
        self._count_caller_uses(graph)

    # -- pass 1 --------------------------------------------------------------

    def _scan_file(
        self, graph: Graph, rel: str, source: bytes, pr: ParseResult,
    ) -> None:
        matches = self._parser.query_matches(pr, _COLLECTION_QUERY)
        for _idx, captures in matches:
            name_nodes = captures.get("collection.name", [])
            body_nodes = captures.get("collection.body", [])
            ctor_nodes = captures.get("collection.ctor", [])

            if not name_nodes or not body_nodes:
                continue

            ctor = pr.text(ctor_nodes[0]) if ctor_nodes else None
            if ctor and ctor not in ("frozenset", "set"):
                continue

            kind = ctor or "set"
            name = pr.text(name_nodes[0])
            line = name_nodes[0].start_point[0] + 1

            values = self._extract_literal_values(body_nodes[0], pr)
            if not values or len(values) > _MAX_VALUES:
                continue

            referenced = self._find_referencing_fns(name, rel, graph, source)

            graph.value_locked_collections.append(ValueLockedCollection(
                name=name,
                file=rel,
                line=line,
                kind=kind,
                values=values,
                referenced_by_fns=referenced,
            ))

    def _extract_literal_values(self, body_node, pr: ParseResult) -> list[str]:
        """Pull string / integer / float literals out of a `set` node's children."""
        values: list[str] = []
        seen: set[str] = set()
        for child in body_node.children:
            if child.type == "string":
                text = pr.text(child)
                # Tree-sitter `string` includes its quotes — strip them.
                stripped = text.strip()
                if len(stripped) >= 2 and stripped[0] in ("'", '"'):
                    stripped = stripped[1:-1]
                # Skip multi-line / escaped — they're rarely domain enums.
                if "\n" in stripped or "\\" in stripped:
                    continue
                if stripped not in seen:
                    seen.add(stripped)
                    values.append(stripped)
            elif child.type in ("integer", "float"):
                text = pr.text(child)
                if text not in seen:
                    seen.add(text)
                    values.append(text)
        return values

    def _find_referencing_fns(
        self, name: str, rel: str, graph: Graph, source: bytes,
    ) -> list[str]:
        """Functions in `rel` whose source text contains a whole-word `name`."""
        file_node = graph.files.get(rel)
        if not file_node:
            return []
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:
            return []
        lines = text.splitlines()
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")

        referenced: list[str] = []
        for fid in file_node.function_ids:
            fn = graph.functions.get(fid)
            if fn is None:
                continue
            # line numbers are 1-based; lists are 0-based
            body = "\n".join(lines[fn.line_start - 1:fn.line_end])
            if pattern.search(body):
                referenced.append(fid)
        return referenced

    # -- pass 2 --------------------------------------------------------------

    def _count_caller_uses(self, graph: Graph) -> None:
        if not graph.value_locked_collections:
            return

        # Build fn_id → list[collection_index] for fast lookup.
        consumer_to_idx: dict[str, list[int]] = {}
        for i, c in enumerate(graph.value_locked_collections):
            for fid in c.referenced_by_fns:
                consumer_to_idx.setdefault(fid, []).append(i)

        if not consumer_to_idx:
            return

        # Walk call edges. For each edge whose target is a consumer fn,
        # extract literal args from the call expression and see if any of
        # them match this collection's values.
        files_seen: dict[int, set[str]] = {}
        for edge in graph.call_edges:
            indices = consumer_to_idx.get(edge.target_fn)
            if not indices:
                continue
            literals = _extract_call_literals(edge.call_site.expression)
            if not literals:
                continue
            caller_file = edge.call_site.file
            for idx in indices:
                col = graph.value_locked_collections[idx]
                value_set = set(col.values)
                hit = False
                for lit in literals:
                    if lit in value_set:
                        col.literal_uses[lit] = col.literal_uses.get(lit, 0) + 1
                        hit = True
                if hit:
                    files_seen.setdefault(idx, set()).add(caller_file)

        for idx, files in files_seen.items():
            graph.value_locked_collections[idx].files_with_uses = sorted(files)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_call_literals(expression: str) -> list[str]:
    """Pull str/int literals out of a call expression string.

    Conservative — only matches simple quoted strings without escapes and
    bare integers. Won't catch f-strings or computed values; that's fine,
    those aren't literal args anyway.
    """
    results: list[str] = []
    for match in _STR_LITERAL_RE.finditer(expression):
        results.append(match.group(1) if match.group(1) is not None else match.group(2))
    for match in _INT_LITERAL_RE.finditer(expression):
        results.append(match.group(0))
    return results


# ---------------------------------------------------------------------------
# Diff helpers — used by impact_check
# ---------------------------------------------------------------------------

def diff_collections(
    before: list[ValueLockedCollection],
    after: list[ValueLockedCollection],
) -> list[dict]:
    """Compare two snapshots and return per-collection change records.

    Each record: {name, file, removed: [...], added: [...], total_literal_uses,
    files_at_risk}. Only includes collections whose values actually changed
    AND that have caller literal uses worth warning about.
    """
    def _id(c: ValueLockedCollection) -> tuple[str, str]:
        return (c.file, c.name)

    before_map = {_id(c): c for c in before}
    after_map = {_id(c): c for c in after}

    changes: list[dict] = []
    for key, after_col in after_map.items():
        before_col = before_map.get(key)
        if before_col is None:
            continue
        before_set = set(before_col.values)
        after_set = set(after_col.values)
        removed = sorted(before_set - after_set)
        added = sorted(after_set - before_set)
        if not removed and not added:
            continue
        # Literal-use stats from BEFORE — that's what's actually at risk.
        affected_uses = sum(
            before_col.literal_uses.get(v, 0) for v in removed
        )
        change: dict = {
            "name": after_col.name,
            "file": after_col.file,
            "removed": removed,
            "added": added,
            "affected_literal_uses": affected_uses,
            "files_at_risk": before_col.files_with_uses if removed else [],
        }
        changes.append(change)
    return changes
