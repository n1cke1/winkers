"""AST expression-uses index — Path 2 of the literal-blind fix.

Wave 3 Path 1 surfaced repo-wide string-literal occurrences via
``ripgrep``-equivalent regex scanning. That works but is structurally
blind: comments, docstring prose, and unrelated identifier substrings
all hit. Path 2 walks the **Python AST**, classifies each ``str``
constant by its syntactic context (comparison vs call argument vs
dict value vs subscript vs match arm), and stores the result in
``.winkers/expressions.json``. ``diff_collections`` consults this
index when present; the regex grep stays as a fallback for
non-Python files.

Scope (CONCEPT.md §2 → §8 Path 2):
- **Python-only.** JS/TS/Java/Go/Rust/C# would need their own AST
  walkers; out of scope for the first cut.
- **Matched-only.** A literal value enters the index iff it appears
  in some ``value_unit.values`` from the current graph. This keeps
  the index bounded — repos full of unrelated ``"GET"`` and
  ``"json"`` strings don't bloat it.
- **Frequency threshold ≥ 3 occurrences across the project.**
  One-off literals aren't load-bearing.
- **Bounded snippets.** Each occurrence carries a 120-char snippet of
  the line for human verification; the AST kind is the precise
  signal.

Cache strategy:
- ``ExpressionsStore.load()`` returns the persisted index (or empty).
- ``build_expressions_index(graph, root)`` always produces a fresh
  index — it's cheap (one pass over .py files) and the staleness
  surface is small.
- The index includes a top-level ``content_hash`` derived from the
  set of value_unit values it's matched against; a different graph
  → different hash → caller knows to refresh.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from pathlib import Path

from pydantic import BaseModel

from winkers.models import Graph
from winkers.store import STORE_DIR

log = logging.getLogger(__name__)

EXPRESSIONS_FILE = "expressions.json"

# Same syntactic context vocabulary used by the warning surface.
KIND_COMPARISON = "comparison"
KIND_CALL_ARG = "call_arg"
KIND_DICT_VALUE = "dict_value"
KIND_DICT_KEY = "dict_key"
KIND_SUBSCRIPT = "subscript"
KIND_MATCH = "match"
KIND_OTHER = "other"

# Minimum repeat count to retain a literal in the index. Singleton
# occurrences aren't load-bearing.
_MIN_REPEAT = 3
_SNIPPET_MAX = 120


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ExpressionUse(BaseModel):
    """One occurrence of a tracked literal in source code."""

    file: str
    line: int
    kind: str       # one of the KIND_* constants above
    context: str    # short snippet of the source line (≤120 chars)


class ExpressionsIndex(BaseModel):
    """Top-level ``.winkers/expressions.json`` shape."""

    content_hash: str = ""
    # value -> [ExpressionUse, ...]
    values: dict[str, list[ExpressionUse]] = {}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ExpressionsStore:
    """Persistence for ``.winkers/expressions.json`` (Path 2 index)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / STORE_DIR / EXPRESSIONS_FILE

    def load(self) -> ExpressionsIndex | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ExpressionsIndex.model_validate(data)
        except Exception as e:
            log.debug("expressions.json malformed (%s); treating as empty", e)
            return None

    def save(self, index: ExpressionsIndex) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            index.model_dump_json(indent=2), encoding="utf-8",
        )
        tmp.replace(self.path)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_expressions_index(
    graph: Graph,
    root: Path,
    *,
    extra_values: set[str] | None = None,
) -> ExpressionsIndex:
    """Walk every Python file and index occurrences of tracked literals.

    The "tracked" set is the union of:
      - all string values across ``graph.value_locked_collections``
      - everything in ``extra_values`` (escape hatch for callers that
        want to track values outside the value_locked detector — e.g.
        a future Wave that promotes Enum members).

    Returns an ``ExpressionsIndex`` whose ``content_hash`` is derived
    from the tracked set, so callers know when to rebuild.
    """
    tracked: set[str] = set(extra_values or ())
    for col in graph.value_locked_collections:
        for v in col.values:
            if isinstance(v, str):
                tracked.add(v)

    if not tracked:
        return ExpressionsIndex()

    visitor = _UseCollector(tracked)

    # Walk every `.py` file under `root`, not just `graph.files` —
    # GraphBuilder prunes files with no functions, but a constant-only
    # ``config.py`` or ``fixtures.py`` is exactly the kind of file
    # that holds load-bearing literal references.
    for path in _iter_py_files(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        lines = source.splitlines()
        visitor.run(tree, rel, lines)

    # Apply the frequency threshold: keep values seen ≥ _MIN_REPEAT times.
    raw = visitor.uses
    filtered = {
        v: items for v, items in raw.items() if len(items) >= _MIN_REPEAT
    }

    content_hash = _hash_tracked_set(tracked)
    return ExpressionsIndex(content_hash=content_hash, values=filtered)


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


class _UseCollector(ast.NodeVisitor):
    """Stateful visitor that classifies each tracked-string occurrence.

    `ast.NodeVisitor.generic_visit` is overridden to track the parent
    so the syntactic-context classifier can look up.
    """

    def __init__(self, tracked: set[str]) -> None:
        self.tracked = tracked
        self.uses: dict[str, list[ExpressionUse]] = {}
        self._parent_stack: list[ast.AST] = []
        self._file: str = ""
        self._lines: list[str] = []

    def run(
        self, tree: ast.AST, rel_path: str, lines: list[str],
    ) -> None:
        self._file = rel_path
        self._lines = lines
        self._parent_stack = []
        self.visit(tree)

    # NodeVisitor overrides -------------------------------------------------

    def generic_visit(self, node: ast.AST) -> None:
        self._parent_stack.append(node)
        try:
            super().generic_visit(node)
        finally:
            self._parent_stack.pop()

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if not isinstance(node.value, str):
            self.generic_visit(node)
            return
        if node.value not in self.tracked:
            self.generic_visit(node)
            return
        kind = self._classify(node)
        # KIND_OTHER skipped: `KIND_OTHER` covers things like the
        # original `VALID_STATUSES = {...}` collection definition,
        # standalone return statements, and simple assignments —
        # not consumer call-sites. Including them would let the
        # collection's own definition push every value over the
        # `_MIN_REPEAT` threshold even on tiny repos. The warning
        # surface cares about distinct CONSUMER usages (comparison /
        # call_arg / dict_value / dict_key / subscript / match).
        if kind == KIND_OTHER:
            self.generic_visit(node)
            return
        line = getattr(node, "lineno", 0)
        snippet = self._snippet(line)
        self.uses.setdefault(node.value, []).append(
            ExpressionUse(file=self._file, line=line, kind=kind, context=snippet),
        )
        self.generic_visit(node)

    # Match-case literals — Python 3.10+ has `MatchValue(value)` whose
    # `value` is the constant. `match x: case "sent":` lands here.
    def visit_MatchValue(self, node: ast.MatchValue) -> None:  # noqa: N802
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            sval = node.value.value
            if sval in self.tracked:
                line = getattr(node, "lineno", 0)
                self.uses.setdefault(sval, []).append(
                    ExpressionUse(
                        file=self._file, line=line, kind=KIND_MATCH,
                        context=self._snippet(line),
                    ),
                )
                # Don't recurse into the constant child — already counted.
                return
        self.generic_visit(node)

    # Internals -------------------------------------------------------------

    def _classify(self, node: ast.Constant) -> str:
        """Look up the immediate parent + grandparent to decide context."""
        if not self._parent_stack:
            return KIND_OTHER
        parent = self._parent_stack[-1]

        # `x == "sent"`, `x in {"a", "b"}`, etc. The ``Compare`` node
        # owns both sides — `node` lands as a comparator OR as the
        # left operand of an ``in`` test.
        if isinstance(parent, ast.Compare):
            if node is parent.left or node in parent.comparators:
                return KIND_COMPARISON

        # Set / list literal used inside a comparison: `x in {"a", "b"}`.
        # Walk up one more level to detect.
        if isinstance(parent, (ast.Set, ast.List, ast.Tuple)):
            if len(self._parent_stack) >= 2:
                gp = self._parent_stack[-2]
                if isinstance(gp, ast.Compare):
                    return KIND_COMPARISON

        # `f("sent")` — the literal is in `args` or `keywords`.
        if isinstance(parent, ast.Call):
            if node in parent.args:
                return KIND_CALL_ARG
            for kw in parent.keywords:
                if kw.value is node:
                    return KIND_CALL_ARG

        # Dict literal — `node` is either a key or a value.
        if isinstance(parent, ast.Dict):
            if node in parent.values:
                return KIND_DICT_VALUE
            if node in parent.keys:
                return KIND_DICT_KEY

        # `obj["sent"]` / `dict["sent"]` — Subscript.slice
        if isinstance(parent, ast.Subscript):
            return KIND_SUBSCRIPT

        return KIND_OTHER

    def _snippet(self, line: int) -> str:
        if not (0 < line <= len(self._lines)):
            return ""
        text = self._lines[line - 1].strip()
        if len(text) > _SNIPPET_MAX:
            text = text[: _SNIPPET_MAX - 3] + "..."
        return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".winkers", "site-packages", "vendor", "third_party",
})


def _iter_py_files(root: Path):
    """Yield every ``.py`` file under ``root``, pruning vendored / build dirs."""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _hash_tracked_set(tracked: set[str]) -> str:
    h = hashlib.sha256()
    for v in sorted(tracked):
        h.update(v.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()
