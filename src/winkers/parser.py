"""Tree-sitter parser wrapper (tree-sitter >= 0.23 API)."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

from tree_sitter import Language, Node, Parser, QueryCursor

from winkers.languages.base import LanguageProfile

log = logging.getLogger(__name__)

# Track query failures across the run — accessible to GraphBuilder so it can
# include them in graph.meta. Without this, broken queries silently return
# empty matches (one such bug went unnoticed in the JS profile until a spike
# diagnosed it). Warn on first occurrence per (language, error) pair.
_query_errors: list[dict] = []
_warned_pairs: set[tuple[str, str]] = set()


def query_errors() -> list[dict]:
    """Return all query errors collected since process start.

    GraphBuilder calls this to surface failures in graph.meta. Resetting is
    not provided — the run is assumed short-lived (one `winkers init`).
    """
    return list(_query_errors)


def _load_language(lang_name: str) -> Language:
    """Load a tree-sitter Language by name."""
    if lang_name == "python":
        import tree_sitter_python as mod
        return Language(mod.language())
    elif lang_name == "typescript":
        import tree_sitter_typescript as mod  # type: ignore[no-redef]
        return Language(mod.language_typescript())
    elif lang_name == "javascript":
        import tree_sitter_javascript as mod  # type: ignore[no-redef]
        return Language(mod.language())
    elif lang_name == "java":
        import tree_sitter_java as mod  # type: ignore[no-redef]
        return Language(mod.language())
    elif lang_name == "go":
        import tree_sitter_go as mod  # type: ignore[no-redef]
        return Language(mod.language())
    elif lang_name == "rust":
        import tree_sitter_rust as mod  # type: ignore[no-redef]
        return Language(mod.language())
    elif lang_name == "csharp":
        import tree_sitter_c_sharp as mod  # type: ignore[no-redef]
        return Language(mod.language())
    else:
        raise ValueError(f"Unsupported language: {lang_name}")


class ParseResult:
    def __init__(self, tree, source: bytes, language: str, profile: LanguageProfile):
        self.tree = tree
        self.source = source
        self.language = language
        self.profile = profile

    def text(self, node: Node) -> str:
        return self.source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


class TreeSitterParser:
    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}
        self._languages: dict[str, Language] = {}

    def _get_parser(self, profile: LanguageProfile) -> Parser:
        lang_name = profile.tree_sitter_language
        if lang_name not in self._parsers:
            lang = _load_language(lang_name)
            self._languages[lang_name] = lang
            self._parsers[lang_name] = Parser(lang)
        return self._parsers[lang_name]

    def _get_language(self, profile: LanguageProfile) -> Language:
        self._get_parser(profile)  # ensures language is loaded
        return self._languages[profile.tree_sitter_language]

    def parse_file(self, path: Path, profile: LanguageProfile) -> ParseResult:
        source = path.read_bytes()
        return self.parse_source(source, profile)

    def parse_source(self, source: bytes, profile: LanguageProfile) -> ParseResult:
        parser = self._get_parser(profile)
        tree = parser.parse(source)
        return ParseResult(tree, source, profile.language, profile)

    def query_matches(
        self, result: ParseResult, query_str: str
    ) -> list[tuple[int, dict[str, list[Node]]]]:
        """Run a query and return per-pattern matches: [(pattern_idx, {name: [nodes]})]."""
        lang = self._get_language(result.profile)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                q = lang.query(query_str)
            cursor = QueryCursor(q)
            return list(cursor.matches(result.tree.root_node))
        except Exception as e:
            _record_query_error(result.profile.language, query_str, e)
            return []

    def query_captures(
        self, result: ParseResult, query_str: str
    ) -> dict[str, list[Node]]:
        """Run a query and return {capture_name: [nodes]}."""
        lang = self._get_language(result.profile)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                q = lang.query(query_str)
            cursor = QueryCursor(q)
            result_caps = cursor.captures(result.tree.root_node)
            return result_caps if isinstance(result_caps, dict) else {}
        except Exception as e:
            _record_query_error(result.profile.language, query_str, e)
            return {}

    def query(
        self, result: ParseResult, query_str: str
    ) -> list[tuple[Node, str]]:
        """Flat list of (node, capture_name) — for backwards compat."""
        caps = self.query_captures(result, query_str)
        return [(node, name) for name, nodes in caps.items() for node in nodes]


def _record_query_error(language: str, query_str: str, exc: Exception) -> None:
    """Log query failure once per (language, error message) and remember it."""
    msg = str(exc)
    pair = (language, msg)
    _query_errors.append({
        "language": language,
        "error": msg,
        "query_excerpt": query_str.strip().split("\n", 1)[0][:80],
    })
    if pair not in _warned_pairs:
        _warned_pairs.add(pair)
        log.warning(
            "tree-sitter query failed for language=%s: %s. "
            "This may indicate a profile/grammar mismatch — affected files "
            "will return zero matches for this query.",
            language, msg,
        )
