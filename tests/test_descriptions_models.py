"""Tests for winkers.descriptions.models — parser + canonicalization."""

import json

from winkers.descriptions.models import (
    Description,
    HardcodedArtifact,
    parse_description_response,
)


# ---------------------------------------------------------------------------
# Parser tolerance
# ---------------------------------------------------------------------------

def _payload(description: str = "x"*40, artifacts=None) -> str:
    return json.dumps({
        "description": description,
        "hardcoded_artifacts": artifacts or [],
    }, ensure_ascii=False)


def test_parses_plain_json():
    raw = _payload(description="The function does X.", artifacts=[
        {"value": "33", "kind": "count", "context": "var counter"},
    ])
    d = parse_description_response(raw)
    assert d is not None
    assert d.description == "The function does X."
    assert len(d.hardcoded_artifacts) == 1
    assert d.hardcoded_artifacts[0].value == "33"


def test_parses_with_markdown_fences():
    """LLMs often wrap output in ```json ... ```; parser strips fences."""
    payload = _payload()
    raw = "```json\n" + payload + "\n```"
    d = parse_description_response(raw)
    assert d is not None


def test_parses_with_bare_fences():
    raw = "```\n" + _payload() + "\n```"
    d = parse_description_response(raw)
    assert d is not None


def test_parses_with_leading_whitespace():
    raw = "   \n\n" + _payload() + "  \n"
    d = parse_description_response(raw)
    assert d is not None


def test_returns_none_on_invalid_json():
    assert parse_description_response("not even json") is None


def test_returns_none_on_missing_description():
    raw = json.dumps({"hardcoded_artifacts": []})
    assert parse_description_response(raw) is None


def test_returns_none_on_invalid_artifact_kind():
    raw = json.dumps({
        "description": "x",
        "hardcoded_artifacts": [
            {"value": "x", "kind": "INVALID_KIND", "context": "y"},
        ],
    })
    assert parse_description_response(raw) is None


def test_empty_artifacts_list_valid():
    """Functions with no couplings return [] — not an error."""
    raw = _payload(artifacts=[])
    d = parse_description_response(raw)
    assert d is not None
    assert d.hardcoded_artifacts == []


# ---------------------------------------------------------------------------
# HardcodedArtifact value handling
# ---------------------------------------------------------------------------

def test_string_value_works():
    a = HardcodedArtifact(value="K_regen", kind="identifier", context="ctx")
    assert a.canonical_key() == "K_regen"


def test_list_value_works():
    a = HardcodedArtifact(
        value=["b", "a", "c"],
        kind="id_list", context="ctx",
    )
    # Sorted for stable cross-unit comparison.
    assert a.canonical_key() == '["a", "b", "c"]'


def test_list_value_canonicalization_is_order_independent():
    """Two artifacts with same set but different list order should match."""
    a = HardcodedArtifact(value=["a", "b"], kind="id_list", context="x")
    b = HardcodedArtifact(value=["b", "a"], kind="id_list", context="y")
    assert a.canonical_key() == b.canonical_key()


def test_surface_optional():
    a = HardcodedArtifact(value="33", kind="count", context="x")
    assert a.surface is None
    b = HardcodedArtifact(value="33", kind="count", context="x",
                          surface="33 переменных")
    assert b.surface == "33 переменных"


def test_artifact_kind_validation():
    """Pydantic enforces ArtifactKind literal."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        HardcodedArtifact(value="x", kind="bogus", context="y")
