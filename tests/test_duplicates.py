"""Tests for winkers.detection.duplicates — AST hash and name similarity."""

from pathlib import Path

from winkers.detection.duplicates import (
    compute_ast_hash,
    find_duplicates,
    name_similarity,
)
from winkers.graph import GraphBuilder
from winkers.models import FunctionNode, Graph
from winkers.resolver import CrossFileResolver

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


# ---------------------------------------------------------------------------
# AST hash
# ---------------------------------------------------------------------------

class TestAstHash:
    def test_same_logic_different_names_same_hash(self):
        source_a = b"""def calculate_price(item_id, qty):
    base = get_base(item_id)
    return base * qty
"""
        source_b = b"""def compute_cost(product_id, amount):
    base = get_base(product_id)
    return base * amount
"""
        fn_a = FunctionNode(
            id="a::calculate_price", file="a.py", name="calculate_price",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        fn_b = FunctionNode(
            id="b::compute_cost", file="b.py", name="compute_cost",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        hash_a = compute_ast_hash(source_a, fn_a, "python")
        hash_b = compute_ast_hash(source_b, fn_b, "python")
        assert hash_a is not None
        assert hash_b is not None
        assert hash_a == hash_b

    def test_different_logic_different_hash(self):
        source_a = b"""def add(a, b):
    return a + b
"""
        source_b = b"""def add(a, b):
    return a * b
"""
        fn_a = FunctionNode(
            id="a::add", file="a.py", name="add",
            kind="function", language="python", line_start=1, line_end=2, params=[],
        )
        fn_b = FunctionNode(
            id="b::add", file="b.py", name="add",
            kind="function", language="python", line_start=1, line_end=2, params=[],
        )
        hash_a = compute_ast_hash(source_a, fn_a, "python")
        hash_b = compute_ast_hash(source_b, fn_b, "python")
        assert hash_a is not None
        assert hash_b is not None
        assert hash_a != hash_b

    def test_comments_ignored(self):
        source_a = b"""def calc(x):
    # compute result
    return x * 2
"""
        source_b = b"""def calc(x):
    # different comment
    return x * 2
"""
        fn = FunctionNode(
            id="a::calc", file="a.py", name="calc",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        hash_a = compute_ast_hash(source_a, fn, "python")
        hash_b = compute_ast_hash(source_b, fn, "python")
        assert hash_a == hash_b

    def test_empty_function_returns_none(self):
        fn = FunctionNode(
            id="a::x", file="a.py", name="x",
            kind="function", language="python", line_start=10, line_end=5, params=[],
        )
        assert compute_ast_hash(b"", fn, "python") is None

    def test_hash_is_16_chars(self):
        source = b"""def foo(x):
    return x + 1
"""
        fn = FunctionNode(
            id="a::foo", file="a.py", name="foo",
            kind="function", language="python", line_start=1, line_end=2, params=[],
        )
        h = compute_ast_hash(source, fn, "python")
        assert h is not None
        assert len(h) == 16

    def test_on_real_fixture(self):
        """AST hash works on fixture functions."""
        pricing_path = PYTHON_FIXTURE / "modules" / "pricing.py"
        source = pricing_path.read_bytes()
        graph = GraphBuilder().build(PYTHON_FIXTURE)
        CrossFileResolver().resolve(graph, str(PYTHON_FIXTURE))

        for fn in graph.functions.values():
            if fn.file == "modules/pricing.py":
                h = compute_ast_hash(source, fn, "python")
                assert h is not None, f"Hash failed for {fn.name}"


# ---------------------------------------------------------------------------
# Name similarity
# ---------------------------------------------------------------------------

class TestNameSimilarity:
    def test_identical_names(self):
        fn_a = FunctionNode(
            id="a::calc", file="a.py", name="calculate_price",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        fn_b = FunctionNode(
            id="b::calc", file="b.py", name="calculate_price",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        assert name_similarity(fn_a, fn_b) == 1.0

    def test_partial_overlap(self):
        fn_a = FunctionNode(
            id="a::calc", file="a.py", name="calculate_temperature_correction",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        fn_b = FunctionNode(
            id="b::calc", file="b.py", name="calculate_pressure_correction",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        sim = name_similarity(fn_a, fn_b)
        # Jaccard of {calculate, temperature, correction} & {calculate, pressure, correction}
        # = 2/4 = 0.5
        assert sim == 0.5

    def test_no_overlap(self):
        fn_a = FunctionNode(
            id="a::x", file="a.py", name="read_config",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        fn_b = FunctionNode(
            id="b::y", file="b.py", name="write_output",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        assert name_similarity(fn_a, fn_b) == 0.0

    def test_high_similarity(self):
        fn_a = FunctionNode(
            id="a::x", file="a.py", name="validate_user_email",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        fn_b = FunctionNode(
            id="b::y", file="b.py", name="validate_user_phone",
            kind="function", language="python", line_start=1, line_end=1, params=[],
        )
        sim = name_similarity(fn_a, fn_b)
        # {validate, user, email} & {validate, user, phone} = 2/4 = 0.5
        assert sim == 0.5


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------

class TestFindDuplicates:
    def test_exact_duplicate_detected(self):
        graph = Graph()
        fn_a = FunctionNode(
            id="a.py::calc", file="a.py", name="calc",
            kind="function", language="python", line_start=1, line_end=3,
            params=[], ast_hash="abcdef1234567890",
        )
        fn_b = FunctionNode(
            id="b.py::compute", file="b.py", name="compute",
            kind="function", language="python", line_start=1, line_end=3,
            params=[], ast_hash="abcdef1234567890",
        )
        graph.functions = {"a.py::calc": fn_a, "b.py::compute": fn_b}

        dupes = find_duplicates(graph, ["a.py::calc"])
        assert len(dupes) == 1
        assert dupes[0].kind == "exact"
        assert dupes[0].similarity == 1.0

    def test_near_duplicate_detected(self):
        graph = Graph()
        fn_a = FunctionNode(
            id="a.py::calculate_temperature", file="a.py",
            name="calculate_temperature",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        fn_b = FunctionNode(
            id="b.py::calculate_pressure", file="b.py",
            name="calculate_pressure",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        graph.functions = {
            "a.py::calculate_temperature": fn_a,
            "b.py::calculate_pressure": fn_b,
        }

        dupes = find_duplicates(graph, ["a.py::calculate_temperature"], name_threshold=0.3)
        assert len(dupes) >= 1
        assert dupes[0].kind == "near"

    def test_no_duplicates(self):
        graph = Graph()
        fn_a = FunctionNode(
            id="a.py::read_file", file="a.py", name="read_file",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        fn_b = FunctionNode(
            id="b.py::send_email", file="b.py", name="send_email",
            kind="function", language="python", line_start=1, line_end=3, params=[],
        )
        graph.functions = {"a.py::read_file": fn_a, "b.py::send_email": fn_b}

        dupes = find_duplicates(graph, ["a.py::read_file"])
        assert len(dupes) == 0
