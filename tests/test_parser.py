"""Tests for TreeSitterParser."""

from pathlib import Path

from winkers.languages.python import PythonProfile
from winkers.parser import TreeSitterParser

FIXTURE = Path(__file__).parent / "fixtures" / "python_project" / "modules" / "pricing.py"


def test_parse_file_returns_tree():
    parser = TreeSitterParser()
    profile = PythonProfile()
    result = parser.parse_file(FIXTURE, profile)
    assert result.tree is not None
    assert result.language == "python"


def test_function_query_finds_functions():
    parser = TreeSitterParser()
    profile = PythonProfile()
    result = parser.parse_file(FIXTURE, profile)
    captures = parser.query(result, profile.function_query)
    fn_names = [result.text(node) for node, cap in captures if cap == "fn.name"]
    assert "calculate_price" in fn_names
    assert "get_base_price" in fn_names
    assert "apply_discount" in fn_names


def test_call_query_finds_calls():
    parser = TreeSitterParser()
    profile = PythonProfile()
    result = parser.parse_file(FIXTURE, profile)
    captures = parser.query(result, profile.call_query)
    call_names = [result.text(node) for node, cap in captures if cap == "call.name"]
    assert "get_base_price" in call_names
    assert "apply_discount" in call_names
