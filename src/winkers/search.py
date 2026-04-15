"""Function search by intent — tokenized matching against the project graph."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from winkers.models import CallEdge, FunctionNode, Graph

# ---------------------------------------------------------------------------
# Identifier splitting
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEP_RE = re.compile(r"[_\-./]+")


def split_identifier(name: str) -> list[str]:
    """Split snake_case, camelCase, PascalCase identifiers into lowercase word tokens.

    Examples:
        split_identifier("calculate_price")       → ["calculate", "price"]
        split_identifier("calculatePrice")         → ["calculate", "price"]
        split_identifier("getHTTPResponse")        → ["get", "http", "response"]
        split_identifier("XMLParser")              → ["xml", "parser"]
    """
    # First split on separators (underscore, dash, dot, slash)
    parts = _SEP_RE.split(name)
    words: list[str] = []
    for part in parts:
        # Then split on camelCase boundaries
        sub = _CAMEL_RE.sub(" ", part).split()
        words.extend(w.lower() for w in sub if w)
    return words


# ---------------------------------------------------------------------------
# Stemming (lightweight)
# ---------------------------------------------------------------------------

# Common suffixes to strip — keeps it dependency-free.
# snowballstemmer is added as an optional dep; we fall back to this if absent.
_SUFFIX_STRIP = [
    ("tion", 3),   # "calculation" → "calcula" (still matches "calculate" tokens)
    ("sion", 3),
    ("ing", 3),
    ("ment", 3),
    ("ness", 3),
    ("able", 3),
    ("ible", 3),
    ("ized", 3),
    ("ise", 3),
    ("ize", 3),
    ("ous", 3),
    ("ful", 3),
    ("less", 4),
    ("ity", 3),
    ("ed", 3),
    ("er", 3),
    ("es", 3),
    ("ly", 3),
    ("s", 4),      # only strip plural -s if remaining stem ≥ 4 chars
]

_stemmer = None
_stemmer_checked = False


def _get_stemmer():
    global _stemmer, _stemmer_checked
    if not _stemmer_checked:
        _stemmer_checked = True
        try:
            import snowballstemmer
            _stemmer = snowballstemmer.stemmer("english")
        except ImportError:
            _stemmer = None
    return _stemmer


def stem(word: str) -> str:
    """Stem a single word. Uses snowballstemmer if available, else simple suffix strip."""
    stemmer = _get_stemmer()
    if stemmer:
        return stemmer.stemWord(word)
    # Fallback: strip longest matching suffix
    for suffix, min_stem in _SUFFIX_STRIP:
        if word.endswith(suffix) and len(word) - len(suffix) >= min_stem:
            return word[: -len(suffix)]
    return word


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Words too generic to help search
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "to", "in", "of", "on", "at",
    "is", "it", "by", "be", "as", "do", "if", "no", "up", "so", "my",
    "new", "get", "set", "def", "self", "this", "that", "from", "with",
})


def tokenize(text: str) -> list[str]:
    """Tokenize free-form text into stemmed lowercase word tokens."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", text)
    result: list[str] = []
    for w in words:
        # Split camelCase/PascalCase before lowering (needs original case)
        parts = split_identifier(w)
        for p in parts:
            if p and p not in _STOP_WORDS:
                result.append(stem(p))
    return result


def tokenize_function(fn: FunctionNode) -> set[str]:
    """Extract all searchable tokens from a function: name + params + docstring + return_type."""
    tokens: set[str] = set()

    # Name tokens (unstemmed — will be stemmed during comparison)
    for w in split_identifier(fn.name):
        tokens.add(stem(w))

    # Param names and type hints
    for p in fn.params:
        for w in split_identifier(p.name):
            tokens.add(stem(w))
        if p.type_hint:
            for w in split_identifier(p.type_hint):
                tokens.add(stem(w))

    # Return type
    if fn.return_type:
        for w in split_identifier(fn.return_type):
            tokens.add(stem(w))

    # Docstring
    if fn.docstring:
        for w in tokenize(fn.docstring):
            tokens.add(w)  # already stemmed by tokenize()

    return tokens


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@dataclass
class Match:
    fn: FunctionNode
    score: float
    callers: int = 0


# Per-process token cache: {fn_id: (name_tokens, fn_tokens, intent_tokens)}
_fn_token_cache: dict[str, tuple[set[str], set[str], set[str] | None]] = {}


def _get_fn_tokens(fn: FunctionNode) -> tuple[set[str], set[str], set[str] | None]:
    """Get cached tokenized data for a function."""
    cached = _fn_token_cache.get(fn.id)
    if cached is not None:
        return cached

    name_tokens = {stem(w) for w in split_identifier(fn.name)}
    fn_tokens = tokenize_function(fn)
    intent_tokens = None
    if fn.intent:
        intent_tokens = set(tokenize(fn.intent))

    result = (name_tokens, fn_tokens, intent_tokens)
    _fn_token_cache[fn.id] = result
    return result


def invalidate_token_cache(fn_ids: list[str] | None = None) -> None:
    """Clear token cache for specific functions or all."""
    if fn_ids is None:
        _fn_token_cache.clear()
    else:
        for fid in fn_ids:
            _fn_token_cache.pop(fid, None)


def search_functions(
    graph: Graph,
    intent: str,
    zone: str = "",
    threshold: float = 0.2,
    max_results: int = 5,
) -> list[Match]:
    """Search graph functions by intent string. Returns top matches above threshold."""
    tokens = tokenize(intent)
    if not tokens:
        return []

    token_set = set(tokens)
    matches: list[Match] = []

    for fn in graph.functions.values():
        if zone and graph.file_zone(fn.file) != zone:
            continue

        name_tokens, fn_tokens, intent_tokens = _get_fn_tokens(fn)
        score = 0.0

        # 1. Name token overlap (strongest signal — 0.5 weight)
        if name_tokens:
            name_overlap = len(token_set & name_tokens) / max(len(token_set), 1)
            score += name_overlap * 0.5

        # 2. Full signature token overlap (params, types, docstring — 0.3 weight)
        if fn_tokens:
            sig_overlap = len(token_set & fn_tokens) / max(len(token_set), 1)
            score += sig_overlap * 0.3

        # 3. Intent field (LLM-generated — 0.4 weight)
        if intent_tokens:
            intent_overlap = len(token_set & intent_tokens) / max(len(token_set), 1)
            score += intent_overlap * 0.4

        # 4. Caller count bonus (well-used functions = more relevant)
        caller_count = len(graph.callers(fn.id))
        if caller_count >= 3:
            score *= 1.1

        if score >= threshold:
            matches.append(Match(fn=fn, score=round(score, 3), callers=caller_count))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:max_results]


# ---------------------------------------------------------------------------
# Pipeline context
# ---------------------------------------------------------------------------

@dataclass
class PipelineContext:
    upstream: list[FunctionNode] = field(default_factory=list)
    downstream: list[FunctionNode] = field(default_factory=list)
    upstream_edges: list[CallEdge] = field(default_factory=list)
    downstream_edges: list[CallEdge] = field(default_factory=list)


def get_pipeline_context(graph: Graph, fn_id: str) -> PipelineContext:
    """One level upstream (callers) + downstream (callees) for a function."""
    ctx = PipelineContext()

    for edge in graph.callers(fn_id):
        caller = graph.functions.get(edge.source_fn)
        if caller:
            ctx.upstream.append(caller)
            ctx.upstream_edges.append(edge)

    for edge in graph.callees(fn_id):
        callee = graph.functions.get(edge.target_fn)
        if callee:
            ctx.downstream.append(callee)
            ctx.downstream_edges.append(edge)

    return ctx


# ---------------------------------------------------------------------------
# Suggestion heuristic
# ---------------------------------------------------------------------------

def build_suggestion(
    intent: str,
    match: Match,
    pipeline: PipelineContext,
) -> str | None:
    """If an upstream function has a param matching intent tokens, suggest calling it."""
    intent_tokens = set(tokenize(intent))
    if not intent_tokens:
        return None

    for upstream_fn in pipeline.upstream:
        param_names = {stem(w) for p in upstream_fn.params for w in split_identifier(p.name)}
        overlap = intent_tokens & param_names
        if overlap:
            param_str = ", ".join(sorted(overlap))
            return (
                f"{upstream_fn.name}() already accepts [{param_str}] as parameter. "
                f"Call {upstream_fn.name}() with different arguments instead of "
                f"reimplementing."
            )
    return None


# ---------------------------------------------------------------------------
# Format response for MCP tool
# ---------------------------------------------------------------------------

def _fn_signature(fn: FunctionNode) -> str:
    params = ", ".join(
        f"{p.name}: {p.type_hint}" if p.type_hint else p.name
        for p in fn.params
    )
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    return f"({params}){ret}"


def format_before_create_response(
    graph: Graph,
    intent: str,
    matches: list[Match],
    zone: str = "",
    root=None,
) -> dict:
    """Build the full before_create response dict."""
    existing = []

    for m in matches:
        pipeline = get_pipeline_context(graph, m.fn.id)
        suggestion = build_suggestion(intent, m, pipeline)

        entry: dict = {
            "function": m.fn.name,
            "file": m.fn.file,
            "line": m.fn.line_start,
            "signature": _fn_signature(m.fn),
            "callers_count": m.callers,
            "score": m.score,
        }

        if m.fn.intent:
            entry["intent"] = m.fn.intent
        if m.fn.docstring:
            entry["docstring"] = m.fn.docstring

        # Pipeline context
        if pipeline.upstream or pipeline.downstream:
            pipe: dict = {}
            if pipeline.upstream:
                pipe["upstream"] = [
                    {
                        "function": fn.name,
                        "signature": _fn_signature(fn),
                        "file": f"{fn.file}:{fn.line_start}",
                    }
                    for fn in pipeline.upstream
                ]
            if pipeline.downstream:
                pipe["downstream"] = [
                    {
                        "function": fn.name,
                        "signature": _fn_signature(fn),
                        "file": f"{fn.file}:{fn.line_start}",
                    }
                    for fn in pipeline.downstream
                ]
            entry["pipeline"] = pipe

        if suggestion:
            entry["suggestion"] = suggestion

        existing.append(entry)

    result: dict = {
        "intent": intent,
        "matches": len(existing),
    }

    if existing:
        result["existing"] = existing
    else:
        result["existing"] = []
        result["note"] = "No existing implementations found matching this intent."

    # Zone conventions
    conventions = _zone_conventions(graph, zone or _guess_zone(graph, matches), root)
    if conventions:
        result["zone_conventions"] = conventions

    return result


def _guess_zone(graph: Graph, matches: list[Match]) -> str:
    """Best-guess zone from top match."""
    if matches:
        return graph.file_zone(matches[0].fn.file)
    return ""


def _zone_conventions(graph: Graph, zone: str, root) -> dict | None:
    """Collect naming patterns + rules + zone intent for a zone."""
    if not zone or zone == "unknown":
        return None

    conventions: dict = {"zone": zone}

    # Naming patterns: extract common prefixes from functions in this zone
    zone_fns = [
        fn for fn in graph.functions.values()
        if graph.file_zone(fn.file) == zone
    ]
    if zone_fns:
        name_words = [split_identifier(fn.name) for fn in zone_fns]
        prefixes: dict[str, int] = {}
        for words in name_words:
            if words:
                prefixes[words[0]] = prefixes.get(words[0], 0) + 1
        common = [(p, c) for p, c in prefixes.items() if c >= 2]
        if common:
            common.sort(key=lambda x: x[1], reverse=True)
            conventions["naming_patterns"] = [
                f"{p}_ (used {c} times)" for p, c in common[:5]
            ]

    # Rules from rules.json
    if root:
        from winkers.conventions import RulesStore
        rules_file = RulesStore(root).load()
        zone_rules = [
            {"id": r.id, "title": r.title, "wrong_approach": r.wrong_approach}
            for r in rules_file.rules
            if zone in r.affects or any(zone in a for a in r.affects)
        ]
        if zone_rules:
            conventions["rules"] = zone_rules

    # Zone intent from semantic.json
    if root:
        from winkers.semantic import SemanticStore
        semantic = SemanticStore(root).load()
        if semantic and zone in semantic.zone_intents:
            intent = semantic.zone_intents[zone]
            conventions["intent"] = {
                "why": intent.why,
                "wrong_approach": intent.wrong_approach,
            }

    return conventions if len(conventions) > 1 else None
