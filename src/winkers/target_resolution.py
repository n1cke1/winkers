"""Intent categorization + target resolution for before_create.

Given a natural-language intent and a graph, figure out:
  1. what the agent wants to do — create / restructure / modify
  2. which concrete files, zones, or functions are referenced

No LLM, no network — pure regex + graph lookup. Used by before_create to return
specific coupling/migration data instead of a generic FTS5 miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from winkers.models import Graph
from winkers.search import stem

IntentCategory = str  # "create" | "restructure" | "modify" | "unknown"

_CREATE_KEYWORDS = frozenset({
    "add", "create", "implement", "new", "build", "introduce", "write",
})
_RESTRUCTURE_KEYWORDS = frozenset({
    "move", "merge", "consolidate", "combine", "split", "extract",
    "reorganize", "relocate", "flatten", "group",
})
_MODIFY_KEYWORDS = frozenset({
    "change", "refactor", "simplify", "rename", "remove", "delete",
    "optimize", "replace", "rewrite", "extend", "modify", "update",
    "fix", "adjust",
})

_MIN_NAME_LEN = 4  # avoid matching short/common tokens like "id", "as", "do"

# Pre-stem the keyword lists once so inflected forms in an intent match via
# the same stemmer. "consolidating" / "consolidate" / "consolidated" all land
# on the same stem, regardless of which dictionary snowballstemmer picked.
_CREATE_STEMS = frozenset(stem(w) for w in _CREATE_KEYWORDS)
_RESTRUCTURE_STEMS = frozenset(stem(w) for w in _RESTRUCTURE_KEYWORDS)
_MODIFY_STEMS = frozenset(stem(w) for w in _MODIFY_KEYWORDS)


@dataclass
class ResolvedTargets:
    paths: list[str] = field(default_factory=list)       # file paths in graph
    functions: list[str] = field(default_factory=list)   # fn_ids
    zones: list[str] = field(default_factory=list)       # zone names

    def is_empty(self) -> bool:
        return not (self.paths or self.functions or self.zones)


def categorize_intent(intent: str) -> IntentCategory:
    """Classify intent into create/restructure/modify/unknown by keyword."""
    if not intent:
        return "unknown"
    words = [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]*", intent)]
    if not words:
        return "unknown"

    stems = {stem(w) for w in words}

    # Priority: restructure > modify > create. Reorganization hints are stronger
    # than generic "change", and both outrank "add" when they co-occur
    # ("add a new module by consolidating X" → restructure).
    if stems & _RESTRUCTURE_STEMS:
        return "restructure"
    if stems & _MODIFY_STEMS:
        return "modify"
    if stems & _CREATE_STEMS:
        return "create"
    return "unknown"


def resolve_targets(intent: str, graph: Graph) -> ResolvedTargets:
    """Find paths, fn_ids, and zone names explicitly mentioned in the intent."""
    result = ResolvedTargets()
    if not intent or not graph:
        return result

    text_lower = intent.lower()

    zones_seen: set[str] = set()
    for path, file_node in graph.files.items():
        if file_node.zone:
            zones_seen.add(file_node.zone)

    # Zones: match either as bare name ("domain") or with trailing slash ("domain/").
    for zone in sorted(zones_seen, key=len, reverse=True):
        if len(zone) < _MIN_NAME_LEN:
            continue
        if _contains_word(text_lower, zone.lower()) or f"{zone.lower()}/" in text_lower:
            result.zones.append(zone)

    # Files: match by full relative path or unique basename.
    basename_to_paths: dict[str, list[str]] = {}
    for path in graph.files:
        basename = PurePosixPath(path).name
        basename_to_paths.setdefault(basename, []).append(path)

    for path in graph.files:
        if path.lower() in text_lower:
            if path not in result.paths:
                result.paths.append(path)

    for basename, paths in basename_to_paths.items():
        if len(basename) < _MIN_NAME_LEN:
            continue
        if _contains_word(text_lower, basename.lower()):
            for p in paths:
                if p not in result.paths:
                    result.paths.append(p)

    # Functions: match by function name (case-insensitive, word-boundary).
    for fn_id, fn in graph.functions.items():
        name = fn.name
        if len(name) < _MIN_NAME_LEN:
            continue
        if _contains_word(text_lower, name.lower()):
            if fn_id not in result.functions:
                result.functions.append(fn_id)

    # Zone expansion: if a zone was named but no explicit files given, expand to
    # all files in that zone — lets "consolidate domain/ files" resolve cleanly.
    if result.zones and not result.paths:
        for zone in result.zones:
            for path, fnode in graph.files.items():
                if fnode.zone == zone and path not in result.paths:
                    result.paths.append(path)

    return result


def _contains_word(haystack: str, needle: str) -> bool:
    """Whole-word match, tolerant of path separators and dots (e.g. 'rules.py')."""
    if not needle:
        return False
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(needle) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, haystack) is not None
