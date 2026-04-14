"""Tests for winkers.search — function search by intent."""

from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.models import FunctionNode, Graph, Param
from winkers.resolver import CrossFileResolver
from winkers.search import (
    Match,
    PipelineContext,
    build_suggestion,
    format_before_create_response,
    get_pipeline_context,
    search_functions,
    split_identifier,
    stem,
    tokenize,
    tokenize_function,
)

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


# ---------------------------------------------------------------------------
# split_identifier
# ---------------------------------------------------------------------------

class TestSplitIdentifier:
    def test_snake_case(self):
        assert split_identifier("calculate_price") == ["calculate", "price"]

    def test_camel_case(self):
        assert split_identifier("calculatePrice") == ["calculate", "price"]

    def test_pascal_case(self):
        assert split_identifier("CalculatePrice") == ["calculate", "price"]

    def test_acronym(self):
        assert split_identifier("getHTTPResponse") == ["get", "http", "response"]

    def test_acronym_start(self):
        assert split_identifier("XMLParser") == ["xml", "parser"]

    def test_single_word(self):
        assert split_identifier("price") == ["price"]

    def test_underscores_and_dots(self):
        assert split_identifier("api.prices") == ["api", "prices"]

    def test_empty(self):
        assert split_identifier("") == []

    def test_numbers(self):
        assert split_identifier("item2price") == ["item2price"]


# ---------------------------------------------------------------------------
# stem
# ---------------------------------------------------------------------------

class TestStem:
    def test_basic_stemming(self):
        # With snowballstemmer installed, "calculation" stems to a root
        stemmed = stem("calculation")
        assert stemmed != "calculation"  # should be shortened

    def test_short_word_unchanged(self):
        result = stem("go")
        assert len(result) <= 2


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_simple_intent(self):
        tokens = tokenize("calculate price")
        assert len(tokens) >= 2

    def test_stop_words_removed(self):
        tokens = tokenize("get the price for an item")
        # "the", "for", "an" are stop words; "get" is also a stop word
        assert all(t not in ("the", "for", "an") for t in tokens)

    def test_camel_case_in_text(self):
        tokens = tokenize("calculatePrice")
        assert len(tokens) >= 2

    def test_empty(self):
        assert tokenize("") == []


# ---------------------------------------------------------------------------
# tokenize_function
# ---------------------------------------------------------------------------

class TestTokenizeFunction:
    def test_extracts_name_params_docstring(self):
        fn = FunctionNode(
            id="test::calc_price",
            file="test.py",
            name="calculate_price",
            kind="function",
            language="python",
            line_start=1,
            line_end=5,
            params=[
                Param(name="item_id", type_hint="int"),
                Param(name="qty", type_hint="int"),
            ],
            return_type="float",
            docstring="Calculate final price with discounts",
        )
        tokens = tokenize_function(fn)
        # Should contain stemmed versions of key words from name, params, docstring
        assert len(tokens) > 3


# ---------------------------------------------------------------------------
# search_functions (on fixture)
# ---------------------------------------------------------------------------

class TestSearchFunctions:
    @pytest.fixture(scope="class")
    def graph(self) -> Graph:
        g = GraphBuilder().build(PYTHON_FIXTURE)
        CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
        return g

    def test_find_calculate_price(self, graph: Graph):
        matches = search_functions(graph, "calculate price")
        assert len(matches) >= 1
        names = [m.fn.name for m in matches]
        assert "calculate_price" in names

    def test_find_by_partial_intent(self, graph: Graph):
        matches = search_functions(graph, "price")
        assert len(matches) >= 1
        # Should find price-related functions
        found_names = {m.fn.name for m in matches}
        assert found_names & {"calculate_price", "get_base_price", "get_price"}

    def test_find_discount(self, graph: Graph):
        matches = search_functions(graph, "apply discount")
        assert len(matches) >= 1
        assert any(m.fn.name == "apply_discount" for m in matches)

    def test_find_stock(self, graph: Graph):
        matches = search_functions(graph, "check stock availability")
        assert len(matches) >= 1
        assert any(m.fn.name == "check_stock" for m in matches)

    def test_no_match(self, graph: Graph):
        matches = search_functions(graph, "blockchain consensus algorithm")
        assert len(matches) == 0

    def test_zone_filter(self, graph: Graph):
        # Search only in a specific zone
        all_matches = search_functions(graph, "price")
        # Filter by zone that contains pricing.py
        zone = graph.file_zone("modules/pricing.py")
        zone_matches = search_functions(graph, "price", zone=zone)
        # Zone-filtered results should be subset
        zone_names = {m.fn.name for m in zone_matches}
        all_names = {m.fn.name for m in all_matches}
        assert zone_names <= all_names

    def test_max_results(self, graph: Graph):
        matches = search_functions(graph, "price", max_results=2)
        assert len(matches) <= 2

    def test_threshold(self, graph: Graph):
        matches = search_functions(graph, "price", threshold=0.9)
        # Very high threshold should filter out low-scoring matches
        for m in matches:
            assert m.score >= 0.9

    def test_scores_are_sorted(self, graph: Graph):
        matches = search_functions(graph, "calculate price")
        scores = [m.score for m in matches]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# pipeline context
# ---------------------------------------------------------------------------

class TestPipelineContext:
    @pytest.fixture(scope="class")
    def graph(self) -> Graph:
        g = GraphBuilder().build(PYTHON_FIXTURE)
        CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
        return g

    def test_calculate_price_pipeline(self, graph: Graph):
        fn_id = None
        for fid, fn in graph.functions.items():
            if fn.name == "calculate_price":
                fn_id = fid
                break
        assert fn_id is not None

        ctx = get_pipeline_context(graph, fn_id)
        # calculate_price calls get_base_price and apply_discount
        downstream_names = {fn.name for fn in ctx.downstream}
        assert "get_base_price" in downstream_names or "apply_discount" in downstream_names

        # calculate_price is called by get_price and check_stock
        upstream_names = {fn.name for fn in ctx.upstream}
        assert len(upstream_names) >= 1

    def test_leaf_function_no_downstream(self, graph: Graph):
        fn_id = None
        for fid, fn in graph.functions.items():
            if fn.name == "get_base_price":
                fn_id = fid
                break
        assert fn_id is not None

        ctx = get_pipeline_context(graph, fn_id)
        assert ctx.downstream == []

    def test_nonexistent_function(self, graph: Graph):
        ctx = get_pipeline_context(graph, "nonexistent::fn")
        assert ctx.upstream == []
        assert ctx.downstream == []


# ---------------------------------------------------------------------------
# suggestion heuristic
# ---------------------------------------------------------------------------

class TestBuildSuggestion:
    def test_suggestion_when_upstream_has_matching_param(self):
        upstream_fn = FunctionNode(
            id="test::reduce_matrix",
            file="calc.py",
            name="reduce_matrix",
            kind="function",
            language="python",
            line_start=30,
            line_end=40,
            params=[
                Param(name="full_matrix"),
                Param(name="element", type_hint="str"),
            ],
        )
        target_fn = FunctionNode(
            id="test::calculate_param",
            file="calc.py",
            name="calculate_param",
            kind="function",
            language="python",
            line_start=45,
            line_end=50,
            params=[Param(name="matrix")],
        )
        match = Match(fn=target_fn, score=0.7, callers=1)
        pipeline = PipelineContext(upstream=[upstream_fn])

        suggestion = build_suggestion("calculate element parameter", match, pipeline)
        assert suggestion is not None
        assert "reduce_matrix" in suggestion

    def test_no_suggestion_when_no_overlap(self):
        upstream_fn = FunctionNode(
            id="test::load_data",
            file="data.py",
            name="load_data",
            kind="function",
            language="python",
            line_start=1,
            line_end=5,
            params=[Param(name="path", type_hint="str")],
        )
        target_fn = FunctionNode(
            id="test::calc",
            file="calc.py",
            name="calc",
            kind="function",
            language="python",
            line_start=10,
            line_end=15,
            params=[],
        )
        match = Match(fn=target_fn, score=0.5, callers=0)
        pipeline = PipelineContext(upstream=[upstream_fn])

        suggestion = build_suggestion("calculate temperature", match, pipeline)
        assert suggestion is None

    def test_no_suggestion_when_no_upstream(self):
        target_fn = FunctionNode(
            id="test::calc",
            file="calc.py",
            name="calc",
            kind="function",
            language="python",
            line_start=10,
            line_end=15,
            params=[],
        )
        match = Match(fn=target_fn, score=0.5, callers=0)
        pipeline = PipelineContext()

        suggestion = build_suggestion("calculate price", match, pipeline)
        assert suggestion is None


# ---------------------------------------------------------------------------
# format_before_create_response
# ---------------------------------------------------------------------------

class TestFormatResponse:
    @pytest.fixture(scope="class")
    def graph(self) -> Graph:
        g = GraphBuilder().build(PYTHON_FIXTURE)
        CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
        return g

    def test_response_structure_with_matches(self, graph: Graph):
        matches = search_functions(graph, "calculate price")
        result = format_before_create_response(graph, "calculate price", matches)
        assert "intent" in result
        assert result["intent"] == "calculate price"
        assert "matches" in result
        assert "existing" in result
        assert len(result["existing"]) >= 1

    def test_response_structure_no_matches(self, graph: Graph):
        matches = search_functions(graph, "blockchain mining")
        result = format_before_create_response(graph, "blockchain mining", matches)
        assert result["matches"] == 0
        assert result["existing"] == []
        assert "note" in result

    def test_existing_entry_fields(self, graph: Graph):
        matches = search_functions(graph, "calculate price")
        result = format_before_create_response(graph, "calculate price", matches)
        entry = result["existing"][0]
        assert "function" in entry
        assert "file" in entry
        assert "line" in entry
        assert "signature" in entry
        assert "score" in entry
