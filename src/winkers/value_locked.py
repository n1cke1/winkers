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
# value_unit promotion (Wave 4b — CONCEPT.md §1, §2)
# ---------------------------------------------------------------------------

VALUE_UNIT_PREFIX = "value:"


def value_unit_id(file: str, name: str) -> str:
    """Stable id for a value_unit: ``value:<file>::<COLLECTION_NAME>``."""
    return f"{VALUE_UNIT_PREFIX}{file}::{name}"


def build_value_units(graph: Graph, root: Path) -> list[dict]:
    """Convert ``graph.value_locked_collections`` into ``units.json`` records.

    Wave 4b adds ``value_unit`` as a fourth unit kind alongside
    ``function_unit`` / ``traceability_unit`` / template / data. The
    units returned here have a structural ``summary`` (so embeddings can
    match queries like "status enum" or "valid types"); ``description``
    is left empty until Wave 4c wires the single LLM-pass author into
    this kind.

    Each unit:
    ```
    {
      "id":            "value:status.py::VALID_STATUSES",
      "kind":          "value_unit",
      "name":          "VALID_STATUSES",
      "anchor":        {"file": "status.py", "line": 3},
      "source_hash":   "<sha256 of source file>",
      "values":        ["draft", "sent", ...],
      "consumer_count": <int>,
      "consumer_files": ["status.py", "service.py", ...],
      "summary":       "...",
      "description":   ""
    }
    ```

    Cross-file consumers (Wave 3.5) feed both ``consumer_count`` and
    the inline summary, so an agent reading the unit's description in
    ``before_create`` / ``find_work_area`` sees the real blast radius
    immediately.
    """
    import hashlib

    units: list[dict] = []
    file_hashes: dict[str, str] = {}

    for col in graph.value_locked_collections:
        consumer_files = sorted({
            fid.split("::", 1)[0] for fid in col.referenced_by_fns
            if "::" in fid
        })
        consumer_count = len(col.referenced_by_fns)
        # Cache file hash per source file — multiple collections may live
        # in the same module.
        file_hash = file_hashes.get(col.file)
        if file_hash is None:
            try:
                content = (root / col.file).read_bytes()
                file_hash = hashlib.sha256(content).hexdigest()
            except OSError:
                file_hash = ""
            file_hashes[col.file] = file_hash

        units.append({
            "id": value_unit_id(col.file, col.name),
            "kind": "value_unit",
            "name": col.name,
            "anchor": {"file": col.file, "line": col.line},
            "source_hash": file_hash,
            "values": list(col.values),
            "consumer_count": consumer_count,
            "consumer_files": consumer_files,
            "summary": _value_unit_summary(col, consumer_files),
            "description": "",
        })

    return units


def _value_unit_summary(col, consumer_files: list[str]) -> str:
    """One-line structural blurb — feeds embeddings until LLM description lands.

    Includes the value names directly so a query like
    ``"draft status flag"`` lands on the right collection without needing
    the agent to know the symbol name.
    """
    n = len(col.values)
    sample = col.values[:6]
    sample_text = ", ".join(repr(v) for v in sample)
    if n > len(sample):
        sample_text += f", … (+{n - len(sample)})"
    return (
        f"{col.name}: {col.kind} of {n} value(s) [{sample_text}]; "
        f"referenced by {len(col.referenced_by_fns)} function(s) "
        f"across {len(consumer_files)} file(s)."
    )


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

class _Detector:
    def __init__(self) -> None:
        self._parser = TreeSitterParser()
        self._profile = PythonProfile()
        # File text cache shared between same-file and cross-file scans.
        # Each .py is read at most once per detector run.
        self._text_cache: dict[str, str] = {}
        self._root: Path | None = None

    def run(self, graph: Graph, root: Path) -> None:
        graph.value_locked_collections = []
        self._text_cache = {}
        self._root = root

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
            self._text_cache[rel] = source.decode("utf-8", errors="replace")
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
        """Functions whose body contains a whole-word reference to `name`.

        Two passes:
          1. **Same-file** — every function defined in `rel`. The
             collection's defining module is the most common consumer
             location.
          2. **Cross-file** — every file that imports `name` from `rel`
             (per `graph.import_edges`). Walks each importing file's
             functions and collects the ones whose body matches
             whole-word `name`.

        Closes Gap 2 from ISSUE_impact_literal_blind: cross-module
        consumers (e.g. `app/services/invoice.py` doing
        ``from .status import VALID_STATUSES``) are now in
        ``referenced_by_fns`` and feed the literal-use counting pass.
        """
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"
        )
        referenced: list[str] = []

        # 1. Same-file consumers.
        file_node = graph.files.get(rel)
        if file_node:
            try:
                text = source.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            referenced.extend(
                self._fns_with_pattern(graph, file_node.function_ids, text, pattern)
            )

        # 2. Cross-file consumers via import edges. Walk every importer
        # of `rel`. We do NOT filter by `edge.names` because the
        # resolver currently leaves it empty on many imports; a
        # whole-word body match on `name` is the same predicate the
        # same-file pass uses, so a quick pre-filter on the file text
        # keeps cost down without needing the import resolver to be
        # name-aware.
        scanned: set[str] = {rel}
        for edge in graph.import_edges:
            if edge.target_file != rel:
                continue
            if edge.source_file in scanned:
                continue
            scanned.add(edge.source_file)
            # If `edge.names` is populated, respect it as a fast
            # negative filter — but never trust an EMPTY list as
            # "imports nothing".
            if edge.names and name not in edge.names:
                continue
            importer = graph.files.get(edge.source_file)
            if importer is None:
                continue
            text = self._read_text(edge.source_file)
            if not text or pattern.search(text) is None:
                continue
            referenced.extend(
                self._fns_with_pattern(
                    graph, importer.function_ids, text, pattern,
                )
            )

        # Dedup while preserving discovery order — same-file fns come
        # first, then cross-file by import-edge iteration order.
        seen: set[str] = set()
        unique: list[str] = []
        for fid in referenced:
            if fid in seen:
                continue
            seen.add(fid)
            unique.append(fid)
        return unique

    def _fns_with_pattern(
        self,
        graph: Graph,
        fn_ids: list[str],
        text: str,
        pattern: re.Pattern,
    ) -> list[str]:
        """Return the subset of `fn_ids` whose body matches `pattern`."""
        if not text or not fn_ids:
            return []
        lines = text.splitlines()
        out: list[str] = []
        for fid in fn_ids:
            fn = graph.functions.get(fid)
            if fn is None:
                continue
            body = "\n".join(lines[fn.line_start - 1:fn.line_end])
            if pattern.search(body):
                out.append(fid)
        return out

    def _read_text(self, rel: str) -> str:
        """Cached text read for cross-file consumer scans."""
        cached = self._text_cache.get(rel)
        if cached is not None:
            return cached
        if self._root is None:
            return ""
        try:
            text = (self._root / rel).read_text(
                encoding="utf-8", errors="replace",
            )
        except OSError:
            text = ""
        self._text_cache[rel] = text
        return text

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
    *,
    root: Path | None = None,
) -> list[dict]:
    """Compare two snapshots and return per-collection change records.

    Each record: {name, file, removed: [...], added: [...],
    affected_literal_uses, files_at_risk}. When `root` is supplied, also
    includes `string_literal_hits` (Path 1 of the literal-blind fix —
    repo-wide quoted-string scan for each removed value, NOT limited to
    same-file consumer call-sites). Only includes collections whose
    values actually changed AND that have caller literal uses worth
    warning about.
    """
    def _id(c: ValueLockedCollection) -> tuple[str, str]:
        return (c.file, c.name)

    before_map = {_id(c): c for c in before}
    after_map = {_id(c): c for c in after}

    # Aggregate all removed values across changes, scan once.
    all_removed: set[str] = set()
    for key, after_col in after_map.items():
        before_col = before_map.get(key)
        if before_col is None:
            continue
        all_removed.update(set(before_col.values) - set(after_col.values))

    repo_hits: dict[str, list[tuple[str, int, str]]] = {}
    if root is not None and all_removed:
        # Filter to string values; integer/float collections are out of
        # scope for the quoted-string repo scan (they'd need a different
        # syntactic predicate).
        string_values = sorted(v for v in all_removed if isinstance(v, str))
        if string_values:
            repo_hits = count_string_literal_occurrences(string_values, root)

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
        if repo_hits:
            per_value: dict[str, list[dict]] = {}
            total = 0
            files: set[str] = set()
            for v in removed:
                hits = repo_hits.get(v, [])
                if not hits:
                    continue
                per_value[v] = [
                    {"file": fp, "line": ln, "snippet": sn}
                    for fp, ln, sn in hits
                ]
                total += len(hits)
                files.update(fp for fp, _, _ in hits)
            if per_value:
                change["string_literal_hits"] = {
                    "total": total,
                    "files": sorted(files),
                    "by_value": per_value,
                }
        changes.append(change)
    return changes


# ---------------------------------------------------------------------------
# Path 1 of the literal-blind fix (ISSUE_impact_literal_blind.md)
# ---------------------------------------------------------------------------

# Source-file extensions that may carry domain-value string literals.
# Code: .py, .ts, .tsx, .js, .jsx, .java, .go, .rs, .cs.
# Data / templates: .sql, .json, .yaml, .yml, .html, .jinja, .j2.
_SCAN_EXTS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".cs",
    ".sql", ".json", ".yaml", ".yml", ".html", ".jinja", ".j2",
)

_SCAN_EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".winkers", "site-packages", "vendor", "third_party",
})

# Bound the work on huge repos. Tunable, but generous defaults: a 2k-file
# repo is the practical ceiling we've seen for `winkers init` lately.
_SCAN_MAX_FILES = 4000
_SCAN_MAX_BYTES_PER_FILE = 256 * 1024  # 256 KB
_SCAN_MAX_HITS_PER_VALUE = 50
_SNIPPET_MAX_CHARS = 120


def count_string_literal_occurrences(
    values: list[str],
    root: Path,
) -> dict[str, list[tuple[str, int, str]]]:
    """Scan the repo for quoted-string occurrences of each removed value.

    Returns ``{value: [(rel_file, line_number, snippet), ...]}``.

    Path 1 of the literal-blind fix — surfaces references invisible to
    the call-graph (bare ``status == "sent"`` comparisons, membership
    tests, fixture data, JSON/SQL string columns). Limited to quoted
    forms via regex, which avoids matching identifier substrings or
    English prose containing the same word unquoted.

    Bounded by `_SCAN_MAX_FILES`, `_SCAN_MAX_BYTES_PER_FILE`,
    `_SCAN_MAX_HITS_PER_VALUE`. On large repos some files / hits will
    be cut off; that's acceptable for a non-blocking warning channel.
    """
    if not values:
        return {}

    # Skip values that contain quote characters — they break the regex
    # boundary assumption and are not realistic enum values anyway.
    safe_values = [v for v in values if v and '"' not in v and "'" not in v]
    if not safe_values:
        return {}

    escaped = [re.escape(v) for v in safe_values]
    # Match `"value"` or `'value'`, with the same quote char on both sides
    # via backreference. Quote char is captured in group 1, value in
    # group 2. Negative-lookbehind/ahead reject quotes inside identifier-
    # like contexts (rare in real code, but keeps noise down).
    pattern = re.compile(r'(["\'])(' + "|".join(escaped) + r')\1')

    hits: dict[str, list[tuple[str, int, str]]] = {v: [] for v in safe_values}

    files_scanned = 0
    for path in _iter_scan_files(root):
        if files_scanned >= _SCAN_MAX_FILES:
            break
        try:
            with path.open("rb") as fh:
                blob = fh.read(_SCAN_MAX_BYTES_PER_FILE)
            text = blob.decode("utf-8", errors="replace")
        except OSError:
            continue
        files_scanned += 1

        # Quick bail: if none of the values appear anywhere, skip line-walk.
        if not any(v in text for v in safe_values):
            continue

        rel = str(path.relative_to(root)).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in pattern.finditer(line):
                val = m.group(2)
                bucket = hits.get(val)
                if bucket is None:
                    continue
                if len(bucket) >= _SCAN_MAX_HITS_PER_VALUE:
                    continue
                snippet = line.strip()
                if len(snippet) > _SNIPPET_MAX_CHARS:
                    snippet = snippet[: _SNIPPET_MAX_CHARS - 3] + "..."
                bucket.append((rel, lineno, snippet))

    return hits


def _iter_scan_files(root: Path):
    """Yield candidate source files under `root`, skipping excluded dirs.

    Uses os.walk for cheap pruning of large excluded subtrees rather
    than `Path.rglob` followed by per-path filtering.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        # Mutate dirnames in place to prune the walk.
        dirnames[:] = [d for d in dirnames if d not in _SCAN_EXCLUDED_DIRS]
        for name in filenames:
            for ext in _SCAN_EXTS:
                if name.endswith(ext):
                    yield Path(dirpath) / name
                    break
