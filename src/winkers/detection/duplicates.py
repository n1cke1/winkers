"""Duplicate detection — AST hash (exact clone) and name similarity (near clone)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from winkers.models import FunctionNode, Graph
from winkers.search import split_identifier

# ---------------------------------------------------------------------------
# AST hash — normalized structure hash for exact clone detection
# ---------------------------------------------------------------------------

def compute_ast_hash(source_bytes: bytes, fn: FunctionNode, language: str) -> str | None:
    """Compute normalized AST hash for a function.

    Normalization:
    - Extract function body from source using line_start/line_end
    - Replace all identifier names with positional placeholders (v0, v1, ...)
    - Remove comments and docstrings (lines starting with # or triple-quotes)
    - Normalize whitespace
    - Hash with sha256
    """
    lines = source_bytes.decode("utf-8", errors="replace").splitlines()
    start = fn.line_start - 1  # 1-based → 0-based
    end = min(fn.line_end, len(lines))
    if start >= end:
        return None

    body_lines = lines[start:end]
    body = "\n".join(body_lines)

    # Remove comments and docstrings
    body = _strip_comments(body, language)

    # Replace identifiers with positional placeholders
    body = _normalize_identifiers(body)

    # Normalize whitespace
    body = re.sub(r"\s+", " ", body).strip()

    if not body:
        return None

    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _strip_comments(text: str, language: str) -> str:
    """Remove comments and docstrings."""
    # Remove triple-quoted strings (Python docstrings)
    text = re.sub(r'"""[\s\S]*?"""', '""', text)
    text = re.sub(r"'''[\s\S]*?'''", "''", text)
    # Remove single-line comments
    if language in ("python",):
        text = re.sub(r"#[^\n]*", "", text)
    else:
        text = re.sub(r"//[^\n]*", "", text)
    # Remove block comments
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return text


_IDENT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")

# Language keywords that should NOT be replaced
_KEYWORDS = frozenset({
    "if", "else", "elif", "for", "while", "return", "def", "class", "import",
    "from", "try", "except", "finally", "with", "as", "yield", "raise",
    "break", "continue", "pass", "lambda", "and", "or", "not", "in", "is",
    "True", "False", "None", "async", "await", "function", "const", "let",
    "var", "new", "this", "super", "extends", "implements", "interface",
    "public", "private", "protected", "static", "void", "int", "float",
    "str", "bool", "list", "dict", "set", "tuple", "type", "struct",
    "fn", "pub", "mut", "self", "match", "enum", "trait",
})


def _normalize_identifiers(text: str) -> str:
    """Replace all user identifiers with positional placeholders."""
    mapping: dict[str, str] = {}
    counter = 0

    def replacer(m: re.Match) -> str:
        nonlocal counter
        name = m.group(1)
        if name in _KEYWORDS:
            return name
        if name not in mapping:
            mapping[name] = f"v{counter}"
            counter += 1
        return mapping[name]

    return _IDENT_RE.sub(replacer, text)


def compute_ast_hash_for_file(
    file_path: Path, graph: Graph, language: str,
) -> dict[str, str]:
    """Compute AST hashes for all functions in a file. Returns {fn_id: hash}."""
    if not file_path.exists():
        return {}
    source = file_path.read_bytes()
    result: dict[str, str] = {}
    file_node = None
    for fnode in graph.files.values():
        if file_path.name in fnode.path or fnode.path in str(file_path):
            file_node = fnode
            break
    if file_node is None:
        return result

    for fn_id in file_node.function_ids:
        fn = graph.functions.get(fn_id)
        if fn is None:
            continue
        h = compute_ast_hash(source, fn, language)
        if h:
            result[fn_id] = h
    return result


# ---------------------------------------------------------------------------
# Name similarity — Jaccard on word tokens
# ---------------------------------------------------------------------------

def name_similarity(fn_a: FunctionNode, fn_b: FunctionNode) -> float:
    """Compare function names as token sets using Jaccard similarity.

    "calculate_temperature_correction" → {"calculate", "temperature", "correction"}
    "calculate_pressure_correction"   → {"calculate", "pressure", "correction"}
    Jaccard: 2/4 = 0.5
    """
    tokens_a = set(split_identifier(fn_a.name))
    tokens_b = set(split_identifier(fn_b.name))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Duplicate scan
# ---------------------------------------------------------------------------

@dataclass
class DuplicateMatch:
    fn_a: FunctionNode
    fn_b: FunctionNode
    kind: str  # "exact" | "near"
    similarity: float  # 1.0 for exact, Jaccard for near


def find_duplicates(
    graph: Graph,
    fn_ids: list[str],
    name_threshold: float = 0.7,
) -> list[DuplicateMatch]:
    """Check new/modified functions against existing graph for duplicates.

    Args:
        graph: Current project graph
        fn_ids: Function IDs to check (new/modified)
        name_threshold: Minimum Jaccard similarity for near-clone warning
    """
    matches: list[DuplicateMatch] = []
    checked_fns = [graph.functions[fid] for fid in fn_ids if fid in graph.functions]

    for new_fn in checked_fns:
        for existing_fn in graph.functions.values():
            if existing_fn.id == new_fn.id:
                continue
            # Exact clone: AST hash match
            if (
                getattr(new_fn, "ast_hash", None)
                and getattr(existing_fn, "ast_hash", None)
                and new_fn.ast_hash == existing_fn.ast_hash
            ):
                matches.append(DuplicateMatch(
                    fn_a=new_fn, fn_b=existing_fn,
                    kind="exact", similarity=1.0,
                ))
                continue
            # Near clone: name similarity
            sim = name_similarity(new_fn, existing_fn)
            if sim >= name_threshold:
                matches.append(DuplicateMatch(
                    fn_a=new_fn, fn_b=existing_fn,
                    kind="near", similarity=round(sim, 3),
                ))

    return matches
