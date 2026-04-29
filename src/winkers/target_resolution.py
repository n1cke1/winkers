"""Intent categorization + target resolution for before_create.

Given a natural-language intent and a graph, figure out:
  1. what the agent wants to do — create / restructure / modify
  2. which concrete files, zones, or functions are referenced

Resolution is a two-stage pipeline, and the stages MERGE:
  - **explicit**: pull out `fn_name()` / `Class.method()` call markers,
    explicit file paths (`app/repos/invoice.py`, Windows backslashes OK),
    and `::` notation (`file.py::fn`). These are precise — an agent that
    follows the convention gets exact targets.
  - **fuzzy**: word-boundary regex match on zone / path-basename /
    function-name from the graph's name dictionary. Fills in what the agent
    described without explicit punctuation.
  - **test filter**: drop test paths/functions unless the intent contains a
    path-like test marker (`tests/`, `test_`, `conftest`) or resolves to a
    test zone. A bare "add tests" does NOT pull in every existing test file.

No LLM, no network — pure regex + graph lookup. Used by before_create to
return specific coupling/migration data instead of a generic FTS5 miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from winkers.models import Graph
from winkers.search import stem

IntentCategory = str  # "create" | "change" | "unknown"

_CREATE_KEYWORDS = frozenset({
    "add", "create", "implement", "new", "build", "introduce", "write",
})
# Anything that touches existing code: structural moves AND in-place edits.
# Two categories collapsed into one — the response shape now adapts to what
# resolve_targets() actually finds (files, functions, or both), not to which
# keyword group fired.
_CHANGE_KEYWORDS = frozenset({
    # structural
    "move", "merge", "consolidate", "combine", "split", "extract",
    "reorganize", "relocate", "flatten", "group",
    # in-place
    "change", "refactor", "simplify", "rename", "remove", "delete",
    "optimize", "replace", "rewrite", "extend", "modify", "update",
    "fix", "adjust",
})

_MIN_NAME_LEN = 4  # avoid matching short/common tokens like "id", "as", "do"

# Pre-stem the keyword lists once so inflected forms in an intent match via
# the same stemmer. "consolidating" / "consolidate" / "consolidated" all land
# on the same stem, regardless of which dictionary snowballstemmer picked.
_CREATE_STEMS = frozenset(stem(w) for w in _CREATE_KEYWORDS)
_CHANGE_STEMS = frozenset(stem(w) for w in _CHANGE_KEYWORDS)

# ---------------------------------------------------------------------------
# Test-path detection
# ---------------------------------------------------------------------------

_TEST_PATH_MARKERS: tuple[str, ...] = (
    "/tests/", "/test/", "tests/", "test/", "test_", "/conftest.py", "conftest.py",
)
# Only *path-like* markers trigger test-inclusion — a bare "add tests" does
# NOT mean "load every test file I've ever written" (that's the Issue #4
# over-return). To include tests the agent must name a path pattern like
# "tests/" or "test_foo.py".
_TEST_INTENT_MARKERS: tuple[str, ...] = (
    "tests/", "test/", "test_", "conftest",
)


def is_test_path(path: str) -> bool:
    """True if the path belongs to a test directory or matches a test-file naming pattern.

    Used by resolve_targets() for test-file filtering and by
    tools._files_block() for prod/test locked_fns split.
    """
    p = path.replace("\\", "/").lower()
    return any(m in p for m in _TEST_PATH_MARKERS)


def _intent_targets_tests(intent_lower: str, zones: list[str] | None = None) -> bool:
    """True if the intent explicitly targets tests — disables the test-path filter."""
    if any(m in intent_lower for m in _TEST_INTENT_MARKERS):
        return True
    if zones and any("test" in z.lower() for z in zones):
        return True
    return False


# ---------------------------------------------------------------------------
# Explicit-target extraction
# ---------------------------------------------------------------------------

# File extensions we try to pick up as explicit paths.
_FILE_EXTS: str = r"py|pyi|ts|tsx|js|jsx|mjs|cjs|java|go|rs|cs"

# "modules/pricing.py::fn_name" or "modules/pricing.py::Class.method"
_DOUBLE_COLON_RE = re.compile(
    rf"([\w./\\-]+\.(?:{_FILE_EXTS}))::([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?"
)
# "Class.method(" — capitalised receiver + dotted call
_CLASS_METHOD_RE = re.compile(
    r"\b([A-Z]\w*)\.([A-Za-z_]\w*)\s*\("
)
# "Class.attribute" — capitalised receiver + dotted access, NO trailing `(`.
# Catches SQLAlchemy `relationship`s and dataclass fields like
# `Client.invoices`, `Invoice.line_items`. Negative lookahead avoids
# double-matching anything `_CLASS_METHOD_RE` already grabbed.
# Lowercase first char on the attr restricts to attribute-like names —
# nested classes (`Foo.Bar`) and constants (`Module.MAX`) intentionally
# don't match here.
_CLASS_ATTR_RE = re.compile(
    r"\b([A-Z]\w*)\.([a-z]\w*)\b(?!\s*\()"
)
# Bare "fn_name(" — a wildcard catch. Stopwords below filter language keywords.
_FN_CALL_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*\("
)
# File path with known extension, forward- OR back-slashes.
_PATH_RE = re.compile(
    rf"[\w./\\-]+\.(?:{_FILE_EXTS})"
)

# Language keywords + common English words that can trivially appear before `(`.
# Kept lowercase for case-insensitive matching. If an identifier is in this set
# we skip it — otherwise regex noise like "if(" or "return(" pollutes targets.
_FN_NAME_STOPWORDS: frozenset[str] = frozenset({
    # Python control flow / decls
    "if", "while", "for", "return", "def", "class", "import", "from", "as",
    "in", "or", "and", "not", "is", "lambda", "try", "except", "finally",
    "with", "yield", "raise", "pass", "assert", "break", "continue",
    "global", "nonlocal", "del", "else", "elif", "print", "type",
    # JS / TS / C# / Java shared
    "function", "var", "let", "const", "await", "async", "typeof", "instanceof",
    "switch", "case", "default", "throw", "new", "this", "super", "void",
    "public", "private", "protected", "static", "final", "extends",
    # Common English verbs / fillers that often appear as "word(...)"
    "true", "false", "null", "none", "undefined", "use", "using", "see",
    "note", "e.g", "i.e",
})
_FN_NAME_MIN_LEN = 3


def extract_explicit_targets(intent: str) -> tuple[set[str], set[str]]:
    """Extract explicit `fn_name()` / `Class.method()` / path references from intent.

    Returns (fn_names, paths). Names may be bare ("calculate_price") or
    dotted ("InvoiceRepo.get_with_items"). Paths are normalised to
    forward-slash.
    """
    if not intent:
        return set(), set()

    fn_names: set[str] = set()
    paths: set[str] = set()

    # 1. `::` notation first — pull both the path and the fn together, then
    # blank the matched region so the later pickers don't duplicate it.
    remaining = intent
    for m in _DOUBLE_COLON_RE.finditer(intent):
        paths.add(m.group(1).replace("\\", "/"))
        if m.group(3):
            fn_names.add(f"{m.group(2)}.{m.group(3)}")
        else:
            fn_names.add(m.group(2))
        remaining = remaining.replace(m.group(0), " " * len(m.group(0)))

    # 2. `Class.method(` — capitalised receiver means this is almost always
    # a class/method, not an arbitrary dotted accessor like `this.foo()`.
    for m in _CLASS_METHOD_RE.finditer(remaining):
        fn_names.add(f"{m.group(1)}.{m.group(2)}")

    # 3. `Class.attribute` (no `(` after) — covers SQLAlchemy `relationship`,
    # Pydantic / dataclass fields, etc. Multiple attrs in a comma/`and`
    # list resolve naturally because finditer iterates each occurrence:
    # "fix Client.invoices, Client.payments, Client.contracts" → 3 targets.
    # Once `attribute_unit` lands in the graph (Wave 5), these resolve to
    # real units; until then they fall through to fuzzy-name matching.
    for m in _CLASS_ATTR_RE.finditer(remaining):
        fn_names.add(f"{m.group(1)}.{m.group(2)}")

    # 4. Bare `fn_name(` — last resort, filtered by stopwords + min length.
    for m in _FN_CALL_RE.finditer(remaining):
        name = m.group(1)
        if len(name) < _FN_NAME_MIN_LEN:
            continue
        if name.lower() in _FN_NAME_STOPWORDS:
            continue
        fn_names.add(name)

    # 4. Explicit paths — always scan the full intent (not `remaining`) so
    # paths from `::` notation also land in `paths`. Duplicates are fine.
    for m in _PATH_RE.finditer(intent):
        paths.add(m.group(0).replace("\\", "/"))

    return fn_names, paths


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

@dataclass
class ResolvedTargets:
    paths: list[str] = field(default_factory=list)       # file paths in graph
    functions: list[str] = field(default_factory=list)   # fn_ids
    zones: list[str] = field(default_factory=list)       # zone names
    # Wave 5a — `Class.attribute` targets (no parens) resolved against
    # graph.class_attributes. Strings of the form "ClassName.attr"; the
    # owning file is also added to `paths` so consumer flow that scans
    # by file still works.
    attributes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.paths or self.functions or self.zones or self.attributes
        )


def categorize_intent(intent: str) -> IntentCategory:
    """Classify intent into create/change/unknown by keyword.

    `change` outranks `create` when both keyword groups appear ("add a new
    module by consolidating X" → change), so structural verbs always win
    over the generic "add".
    """
    if not intent:
        return "unknown"
    words = [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]*", intent)]
    if not words:
        return "unknown"

    stems = {stem(w) for w in words}

    if stems & _CHANGE_STEMS:
        return "change"
    if stems & _CREATE_STEMS:
        return "create"
    return "unknown"


def resolve_targets(intent: str, graph: Graph) -> ResolvedTargets:
    """Find paths, fn_ids, and zones referenced in the intent.

    Pipeline:
      1. Extract explicit markers (`fn()`, `Class.method()`, paths, `::` notation).
      2. Resolve explicit markers against the graph first — these are precise.
      3. Run fuzzy word-boundary matching over zones, paths, and fn names to
         fill in anything the agent described without explicit punctuation.
      4. Filter out test paths/functions unless the intent uses a path-like
         test marker (`tests/`, `test_`, `conftest`) or the resolved zone is
         a test zone.

    Explicit and fuzzy results are MERGED — an explicit `calc()` marker plus
    a bare `pricing.py` basename both land in the result.
    """
    result = ResolvedTargets()
    if not intent or not graph:
        return result

    text_lower = intent.lower()
    explicit_fns, explicit_paths = extract_explicit_targets(intent)

    intent_wants_tests = _intent_targets_tests(text_lower) or any(
        is_test_path(p) for p in explicit_paths
    )

    if explicit_fns or explicit_paths:
        _resolve_explicit(
            result, graph, explicit_fns, explicit_paths,
            include_tests=intent_wants_tests,
        )

    _resolve_fuzzy(result, graph, text_lower)

    # Bare "add tests" doesn't count (see _TEST_INTENT_MARKERS rationale).
    if not (intent_wants_tests or _intent_targets_tests(text_lower, result.zones)):
        result.paths = [p for p in result.paths if not is_test_path(p)]
        result.functions = [
            fid for fid in result.functions
            if fid in graph.functions and not is_test_path(graph.functions[fid].file)
        ]

    return result


def _resolve_explicit(
    result: ResolvedTargets,
    graph: Graph,
    explicit_fns: set[str],
    explicit_paths: set[str],
    *,
    include_tests: bool,
) -> None:
    """Populate result from explicit fn/path markers in the intent."""
    # Paths: exact, case-insensitive, then basename fallback for bare filenames.
    for raw_path in explicit_paths:
        if not include_tests and is_test_path(raw_path):
            continue
        for gpath in _match_explicit_path(raw_path, graph):
            if not include_tests and is_test_path(gpath):
                continue
            if gpath not in result.paths:
                result.paths.append(gpath)

    # Functions: bare name OR Class.method. Also auto-include owning files
    # so _files_block downstream gets migration_cost info even when the
    # agent didn't spell the path.
    fn_files: set[str] = set()
    matched_names: set[str] = set()
    for name in explicit_fns:
        for fn_id, fn in graph.functions.items():
            if not _fn_name_matches(fn, name):
                continue
            if not include_tests and is_test_path(fn.file):
                continue
            if fn_id not in result.functions:
                result.functions.append(fn_id)
            fn_files.add(fn.file)
            matched_names.add(name)

    # Wave 5a — Class.attribute resolution. Anything matching `X.y` that
    # didn't land on a real function is tried against class_attributes.
    # Both the attribute name AND its owning file go to the result so
    # downstream consumers (`scope`, `before_create.files_block`) can
    # treat the file as the work area.
    for name in explicit_fns - matched_names:
        if "." not in name:
            continue
        for attr in graph.class_attributes:
            if attr.name != name:
                continue
            if not include_tests and is_test_path(attr.file):
                continue
            if attr.name not in result.attributes:
                result.attributes.append(attr.name)
            fn_files.add(attr.file)

    for file in fn_files:
        if file not in result.paths:
            result.paths.append(file)


def _match_explicit_path(raw_path: str, graph: Graph) -> list[str]:
    """Resolve a raw path string from intent text to graph file paths.

    Order: exact match → case-insensitive full-path → basename match (only
    when the raw path has no slashes, i.e. the agent wrote just "pricing.py").
    A basename may resolve to multiple graph files if ambiguous.
    """
    if raw_path in graph.files:
        return [raw_path]
    lower = raw_path.lower()
    for gpath in graph.files:
        if gpath.lower() == lower:
            return [gpath]
    if "/" in raw_path or "\\" in raw_path:
        return []
    matches: list[str] = []
    for gpath in graph.files:
        base = gpath.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base == lower:
            matches.append(gpath)
    return matches


def _fn_name_matches(fn, name: str) -> bool:
    """True if `name` refers to this FunctionNode (bare or Class.method)."""
    if "." in name:
        cls, method = name.rsplit(".", 1)
        return fn.class_name == cls and fn.name == method
    return fn.name == name


def _resolve_fuzzy(
    result: ResolvedTargets,
    graph: Graph,
    text_lower: str,
) -> None:
    """Word-boundary matching against zone/path/fn-name dictionary.

    Kept as a fallback when the intent doesn't contain explicit markers —
    agents sometimes say "fix pricing" without naming the function.
    """
    zones_seen: set[str] = set()
    for _path, file_node in graph.files.items():
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


def _contains_word(haystack: str, needle: str) -> bool:
    """Whole-word match, tolerant of path separators and dots (e.g. 'rules.py')."""
    if not needle:
        return False
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(needle) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, haystack) is not None
