"""Scanner for class definitions + class-body attributes (Wave 5a).

Picks out two patterns from each Python file:

1. ``class Foo(Base): ...`` — registered as a ``ClassDefinition`` so an
   agent intent of "audit Client class" or "rename Order" resolves to
   a real graph node.
2. ``attr_name [: annotation] = constructor(...)`` inside a class body
   — registered as a ``ClassAttribute``. Captures SQLAlchemy
   ``relationship``, Pydantic ``Field``, ``Mapped[...]``-style attrs,
   dataclass ``field()``, and any other call-RHS class-body
   assignment. Plain literal-only assignments (``MAX = 100``) are
   skipped: that's value_locked territory or unimportant for unit
   resolution.

Multi-language story: Python only for now. JS/TS/Java/Go/Rust/C# can
be added later by wiring per-language profile queries — the units
schema is language-agnostic.
"""

from __future__ import annotations

import logging
from pathlib import Path

from winkers.languages.python import PythonProfile
from winkers.models import ClassAttribute, ClassDefinition, Graph
from winkers.parser import ParseResult, TreeSitterParser

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def detect_class_attrs(graph: Graph, root: Path) -> None:
    """Populate ``graph.class_definitions`` and ``graph.class_attributes``.

    Idempotent — clears existing lists first. Same shape as
    ``detect_value_locked``: parse each .py file once, walk class
    nodes, emit records.
    """
    detector = _ClassAttrsScanner()
    detector.run(graph, root)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

class _ClassAttrsScanner:
    def __init__(self) -> None:
        self._parser = TreeSitterParser()
        self._profile = PythonProfile()

    def run(self, graph: Graph, root: Path) -> None:
        graph.class_definitions = []
        graph.class_attributes = []

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
            self._scan_file(graph, rel, pr)

    def _scan_file(self, graph: Graph, rel: str, pr: ParseResult) -> None:
        """Walk class_definition nodes; emit one ClassDefinition + N
        ClassAttribute per class."""
        root = pr.tree.root_node
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "class_definition":
                self._emit_class(graph, rel, node, pr)
                # Don't descend into nested classes for attribute scan —
                # nested-class attrs aren't a meaningful unit anchor.
                continue
            for child in node.children:
                stack.append(child)

    def _emit_class(
        self, graph: Graph, rel: str, cls_node, pr: ParseResult,
    ) -> None:
        name_node = cls_node.child_by_field_name("name")
        if name_node is None:
            return
        class_name = pr.text(name_node)
        line_start = cls_node.start_point[0] + 1
        line_end = cls_node.end_point[0] + 1

        bases = self._extract_bases(cls_node, pr)
        graph.class_definitions.append(ClassDefinition(
            name=class_name,
            file=rel,
            line_start=line_start,
            line_end=line_end,
            base_classes=bases,
        ))

        body = cls_node.child_by_field_name("body")
        if body is None:
            return
        for stmt in body.children:
            if stmt.type != "expression_statement":
                continue
            for child in stmt.children:
                attr = self._try_extract_attribute(
                    child, class_name, rel, pr,
                )
                if attr is not None:
                    graph.class_attributes.append(attr)

    def _extract_bases(self, cls_node, pr: ParseResult) -> list[str]:
        """Read ``argument_list`` children of the class header as base names."""
        bases: list[str] = []
        sup = cls_node.child_by_field_name("superclasses")
        if sup is None:
            return bases
        for child in sup.children:
            if child.type in ("identifier", "attribute"):
                bases.append(pr.text(child))
        return bases

    def _try_extract_attribute(
        self, node, class_name: str, rel: str, pr: ParseResult,
    ) -> ClassAttribute | None:
        """Detect ``name = call(...)`` or ``name: T = call(...)`` patterns.

        Returns None for anything that isn't a call-RHS assignment.
        """
        if node.type == "assignment":
            return self._from_assignment(node, class_name, rel, pr)
        # tree-sitter Python wraps ``a: T = b()`` differently per version;
        # guard for both ``annotated_assignment`` (older) and
        # ``assignment`` with a `type` field (newer 0.21+).
        if node.type == "annotated_assignment":
            return self._from_annotated(node, class_name, rel, pr)
        return None

    def _from_assignment(
        self, asgn, class_name: str, rel: str, pr: ParseResult,
    ) -> ClassAttribute | None:
        left = asgn.child_by_field_name("left")
        right = asgn.child_by_field_name("right")
        type_node = asgn.child_by_field_name("type")
        if left is None or right is None or left.type != "identifier":
            return None
        if right.type != "call":
            return None
        attr_name = pr.text(left)
        ctor = self._call_ctor_name(right, pr)
        if not ctor:
            return None
        annotation = pr.text(type_node) if type_node is not None else ""
        return ClassAttribute(
            name=f"{class_name}.{attr_name}",
            class_name=class_name,
            attr_name=attr_name,
            file=rel,
            line=asgn.start_point[0] + 1,
            ctor=ctor,
            annotation=annotation,
        )

    def _from_annotated(
        self, asgn, class_name: str, rel: str, pr: ParseResult,
    ) -> ClassAttribute | None:
        # annotated_assignment children layout: [target, ":", annotation, "=", value]
        target = None
        annotation_node = None
        value = None
        for child in asgn.children:
            ftype = child.type
            if ftype == "identifier" and target is None:
                target = child
            elif ftype == "type":
                annotation_node = child
            elif ftype == "call":
                value = child
        if target is None or value is None:
            return None
        attr_name = pr.text(target)
        ctor = self._call_ctor_name(value, pr)
        if not ctor:
            return None
        annotation = (
            pr.text(annotation_node) if annotation_node is not None else ""
        )
        return ClassAttribute(
            name=f"{class_name}.{attr_name}",
            class_name=class_name,
            attr_name=attr_name,
            file=rel,
            line=asgn.start_point[0] + 1,
            ctor=ctor,
            annotation=annotation,
        )

    def _call_ctor_name(self, call_node, pr: ParseResult) -> str:
        """Resolve the callable name of a ``call`` node.

        Returns the bare identifier for ``relationship(...)`` and the
        attribute name for ``orm.relationship(...)``. Empty string if
        the function position is something we can't name (e.g.
        ``f()()``).
        """
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return ""
        if fn.type == "identifier":
            return pr.text(fn)
        if fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            if attr is not None:
                return pr.text(attr)
        return ""


# ---------------------------------------------------------------------------
# Unit builders (Wave 5a-style — structural, no LLM)
# ---------------------------------------------------------------------------

CLASS_UNIT_PREFIX = "class:"
ATTR_UNIT_PREFIX = "attr:"


def class_unit_id(file: str, name: str) -> str:
    """Stable id: ``class:<file>::<ClassName>``."""
    return f"{CLASS_UNIT_PREFIX}{file}::{name}"


def attribute_unit_id(file: str, class_name: str, attr_name: str) -> str:
    """Stable id: ``attr:<file>::<ClassName>.<attr>``."""
    return f"{ATTR_UNIT_PREFIX}{file}::{class_name}.{attr_name}"


def build_class_units(graph: Graph, root: Path) -> list[dict]:
    """Convert ``graph.class_definitions`` into ``units.json`` records.

    Each unit:
    ```
    {
      "id":          "class:app/repos/client.py::Client",
      "kind":        "class_unit",
      "name":        "Client",
      "anchor":      {"file": ..., "line": 12},
      "source_hash": "<sha256 of the class slice>",
      "base_classes": ["Base", "TimestampMixin"],
      "method_count": 5,
      "attribute_count": 3,
      "summary":     "Client (line 12-89) — 5 methods, 3 attributes; bases: Base, TimestampMixin.",
      "description": ""
    }
    ```

    `summary` carries the structural blurb so embeddings can match
    queries like "client model" or "user repo" without a separate LLM
    description call (LLM authoring lands in Wave 4c when it's wired
    for class kinds).
    """
    import hashlib

    units: list[dict] = []
    file_text_cache: dict[str, str] = {}

    method_counts = _count_methods_per_class(graph)
    attribute_counts = _count_attributes_per_class(graph)

    for cls in graph.class_definitions:
        text = _read_class_slice(
            file_text_cache, root, cls.file, cls.line_start, cls.line_end,
        )
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        n_methods = method_counts.get((cls.file, cls.name), 0)
        n_attrs = attribute_counts.get((cls.file, cls.name), 0)
        units.append({
            "id": class_unit_id(cls.file, cls.name),
            "kind": "class_unit",
            "name": cls.name,
            "anchor": {"file": cls.file, "line": cls.line_start},
            "source_hash": source_hash,
            "base_classes": list(cls.base_classes),
            "method_count": n_methods,
            "attribute_count": n_attrs,
            "summary": _class_summary(cls, n_methods, n_attrs),
            "description": "",
        })

    return units


def build_attribute_units(graph: Graph, root: Path) -> list[dict]:
    """Convert ``graph.class_attributes`` into ``units.json`` records.

    Each unit:
    ```
    {
      "id":           "attr:app/repos/client.py::Client.invoices",
      "kind":         "attribute_unit",
      "name":         "Client.invoices",
      "class_name":   "Client",
      "attr_name":    "invoices",
      "anchor":       {"file": ..., "line": 17},
      "source_hash":  "<sha256 of the assignment line>",
      "ctor":         "relationship",
      "annotation":   "Mapped[List[Invoice]]",
      "summary":      "Client.invoices — relationship; annotation: Mapped[List[Invoice]].",
      "description":  ""
    }
    ```
    """
    import hashlib

    units: list[dict] = []
    file_text_cache: dict[str, str] = {}

    for attr in graph.class_attributes:
        line_text = _read_line(file_text_cache, root, attr.file, attr.line)
        source_hash = hashlib.sha256(line_text.encode("utf-8")).hexdigest() \
            if line_text else ""
        units.append({
            "id": attribute_unit_id(attr.file, attr.class_name, attr.attr_name),
            "kind": "attribute_unit",
            "name": attr.name,
            "class_name": attr.class_name,
            "attr_name": attr.attr_name,
            "anchor": {"file": attr.file, "line": attr.line},
            "source_hash": source_hash,
            "ctor": attr.ctor,
            "annotation": attr.annotation,
            "summary": _attribute_summary(attr),
            "description": "",
        })
    return units


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_methods_per_class(graph: Graph) -> dict[tuple[str, str], int]:
    """Count graph functions by (file, class_name) — methods only."""
    counts: dict[tuple[str, str], int] = {}
    for fn in graph.functions.values():
        if not fn.class_name:
            continue
        key = (fn.file, fn.class_name)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _count_attributes_per_class(graph: Graph) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for attr in graph.class_attributes:
        key = (attr.file, attr.class_name)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _class_summary(cls: ClassDefinition, n_methods: int, n_attrs: int) -> str:
    bases_text = (
        f"; bases: {', '.join(cls.base_classes)}" if cls.base_classes else ""
    )
    return (
        f"{cls.name} (lines {cls.line_start}-{cls.line_end}) — "
        f"{n_methods} method(s), {n_attrs} attribute(s){bases_text}."
    )


def _attribute_summary(attr: ClassAttribute) -> str:
    bits = [f"{attr.name} — {attr.ctor}"]
    if attr.annotation:
        bits.append(f"annotation: {attr.annotation}")
    return "; ".join(bits) + "."


def _read_class_slice(
    cache: dict[str, str], root: Path, rel: str, start: int, end: int,
) -> str:
    text = cache.get(rel)
    if text is None:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        cache[rel] = text
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[start - 1: end])


def _read_line(
    cache: dict[str, str], root: Path, rel: str, line: int,
) -> str:
    text = cache.get(rel)
    if text is None:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        cache[rel] = text
    if not text:
        return ""
    lines = text.splitlines()
    if 0 < line <= len(lines):
        return lines[line - 1]
    return ""
