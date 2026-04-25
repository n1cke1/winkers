"""Tests for winkers.descriptions.aggregator — coupling detection."""

from winkers.descriptions.aggregator import (
    detect_couplings,
    proposed_to_unit,
)


def _fn_unit(file: str, name: str, artifacts: list) -> dict:
    return {
        "id": f"{file}::{name}",
        "kind": "function_unit",
        "anchor": {"file": file, "fn": name},
        "hardcoded_artifacts": artifacts,
    }


def _tpl_unit(file: str, sec: str, artifacts: list) -> dict:
    return {
        "id": f"template:{file}#{sec}",
        "kind": "traceability_unit",
        "source_files": [file],
        "hardcoded_artifacts": artifacts,
    }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detects_value_across_two_files():
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "42", "kind": "count", "context": "x", "surface": "42 cases"},
        ]),
        _tpl_unit("b.html", "main", [
            {"value": "42", "kind": "count", "context": "y", "surface": "42 cases"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert len(clusters) == 1
    assert clusters[0].canonical_value == "42"
    assert clusters[0].file_count == 2


def test_skips_within_file_only_clusters():
    """Two artifacts in the SAME file aren't a cross-file coupling."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "42", "kind": "count", "context": "x", "surface": "42 cases"},
        ]),
        _fn_unit("a.py", "f2", [
            {"value": "42", "kind": "count", "context": "y", "surface": "42 cases"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert clusters == []


def test_filters_generic_values():
    """0 / 1 / True / False / "" are filtered as too generic."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "0", "kind": "count", "context": "x"},
            {"value": "True", "kind": "phrase", "context": "y"},
            {"value": "1", "kind": "count", "context": "z"},
        ]),
        _tpl_unit("b.html", "main", [
            {"value": "0", "kind": "count", "context": "p"},
            {"value": "True", "kind": "phrase", "context": "q"},
            {"value": "1", "kind": "count", "context": "r"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert clusters == []


def test_id_list_canonicalization_matches_different_orders():
    """[a,b,c] and [c,a,b] should land in the same cluster after sort."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": ["PT1", "PT2", "T5"], "kind": "id_list", "context": "x"},
        ]),
        _tpl_unit("b.html", "main", [
            {"value": ["T5", "PT1", "PT2"], "kind": "id_list", "context": "y"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert len(clusters) == 1
    assert clusters[0].file_count == 2
    assert clusters[0].canonical_value == '["PT1", "PT2", "T5"]'


def test_min_files_threshold():
    """min_files=3 skips two-file clusters."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "42", "kind": "count", "context": "x", "surface": "42 cases"},
        ]),
        _fn_unit("b.py", "f2", [
            {"value": "42", "kind": "count", "context": "y", "surface": "42 cases"},
        ]),
    ]
    assert detect_couplings(units, min_files=2)
    assert detect_couplings(units, min_files=3) == []


def test_clusters_sorted_by_file_count_desc():
    """Most cross-cutting clusters come first."""
    units = [
        # "many" — appears in 3 files
        _fn_unit("a.py", "f1", [{"value": "many", "kind": "identifier", "context": "x"}]),
        _fn_unit("b.py", "f2", [{"value": "many", "kind": "identifier", "context": "y"}]),
        _tpl_unit("c.html", "main", [{"value": "many", "kind": "identifier", "context": "z"}]),
        # "few" — appears in 2 files
        _fn_unit("d.py", "f3", [{"value": "few", "kind": "identifier", "context": "x"}]),
        _fn_unit("e.py", "f4", [{"value": "few", "kind": "identifier", "context": "y"}]),
    ]
    clusters = detect_couplings(units)
    assert clusters[0].canonical_value == "many"
    assert clusters[1].canonical_value == "few"


def test_primary_kind_is_majority():
    """When kinds differ across hits, primary_kind = mode."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "x", "kind": "count", "context": "1"},
        ]),
        _fn_unit("b.py", "f2", [
            {"value": "x", "kind": "count", "context": "2"},
        ]),
        _tpl_unit("c.html", "main", [
            {"value": "x", "kind": "phrase", "context": "3"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert clusters[0].primary_kind == "count"  # 2/3 hits
    assert clusters[0].kind_uniformity == 2 / 3


def test_skips_malformed_artifacts():
    """Bad artifact dicts are skipped, not crash the run."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "42", "kind": "count"},  # missing 'context'
            {"value": "ok", "kind": "identifier", "context": "good"},
        ]),
    ]
    # Just shouldn't raise.
    clusters = detect_couplings(units)
    assert clusters == []  # only one valid artifact, no other unit


# ---------------------------------------------------------------------------
# proposed_to_unit
# ---------------------------------------------------------------------------

def test_proposed_to_unit_has_stable_id():
    """Same input → same id (id is sha256-based, deterministic)."""
    units = [
        _fn_unit("a.py", "f1", [{"value": "42", "kind": "count", "context": "x", "surface": "42 cases"}]),
        _fn_unit("b.py", "f2", [{"value": "42", "kind": "count", "context": "y", "surface": "42 cases"}]),
    ]
    clusters = detect_couplings(units)
    u1 = proposed_to_unit(clusters[0])
    u2 = proposed_to_unit(clusters[0])
    assert u1["id"] == u2["id"]


def test_bare_numeric_without_surface_skipped():
    """Pure number "2" with no surface text → not clustered (false-positive
    risk: same number appears in unrelated domains)."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "2", "kind": "threshold", "context": "tolerance"},
        ]),
        _fn_unit("b.py", "f2", [
            {"value": "2", "kind": "count", "context": "column count"},
        ]),
    ]
    assert detect_couplings(units) == []


def test_numeric_with_surface_kept():
    """Number `33` with surface text "33 переменных" IS load-bearing —
    cluster it across files."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "33", "kind": "count", "context": "var count",
             "surface": "33 переменных"},
        ]),
        _tpl_unit("b.html", "main", [
            {"value": "33", "kind": "count", "context": "displayed counter",
             "surface": "33 переменных"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert len(clusters) == 1
    assert clusters[0].canonical_value == "33"


def test_identifier_kept_even_when_short():
    """Short identifier strings are NOT bare numerics — they're real names."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": "x", "kind": "identifier", "context": "config key"},
        ]),
        _fn_unit("b.py", "f2", [
            {"value": "x", "kind": "identifier", "context": "API field"},
        ]),
    ]
    # Note: "x" is not in _GENERIC_VALUES, not a bare numeric → cluster keeps it.
    clusters = detect_couplings(units)
    assert len(clusters) == 1
    assert clusters[0].canonical_value == "x"


def test_id_list_with_one_short_name_kept():
    """A list value never falls under bare-numeric filter even if elements are short."""
    units = [
        _fn_unit("a.py", "f1", [
            {"value": ["a", "b"], "kind": "id_list", "context": "keys"},
        ]),
        _tpl_unit("c.html", "main", [
            {"value": ["b", "a"], "kind": "id_list", "context": "form fields"},
        ]),
    ]
    clusters = detect_couplings(units)
    assert len(clusters) == 1


def test_proposed_to_unit_schema():
    """Output has the expected traceability_unit shape."""
    units = [
        _fn_unit("a.py", "f1", [{"value": "42", "kind": "count", "context": "x", "surface": "42 cases"}]),
        _fn_unit("b.py", "f2", [{"value": "42", "kind": "count", "context": "y", "surface": "42 cases"}]),
    ]
    out = proposed_to_unit(detect_couplings(units)[0])
    assert out["kind"] == "traceability_unit"
    assert out["meta"]["origin"] == "auto-detected"
    assert out["meta"]["primary_kind"] == "count"
    assert out["meta"]["file_count"] == 2
    assert sorted(out["source_files"]) == ["a.py", "b.py"]
    assert len(out["consumers"]) == 2
