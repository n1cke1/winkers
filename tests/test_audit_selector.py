"""Tests for winkers.audit.selector — packet builder."""

from __future__ import annotations

from winkers.audit.selector import build_packet


def _fn_unit(file: str, name: str, artifacts=None) -> dict:
    return {
        "id": f"{file}::{name}",
        "kind": "function_unit",
        "anchor": {"file": file, "fn": name},
        "description": f"desc for {name}",
        "hardcoded_artifacts": artifacts or [],
    }


def _tpl_unit(file: str, sec: str) -> dict:
    return {
        "id": f"template:{file}#{sec}",
        "kind": "traceability_unit",
        "source_files": [file],
        "description": f"section {sec}",
    }


def _coupling(consumers: list[tuple[str, str]], value: str = "x") -> dict:
    """Build a synthetic coupling unit. consumers: list of (file, anchor)."""
    return {
        "id": f"coupling:identifier:{abs(hash(value)) % 10000:04x}",
        "kind": "traceability_unit",
        "source_files": list({c[0] for c in consumers}),
        "consumers": [
            {"file": f, "anchor": a, "what_to_check": f"check {a}"}
            for f, a in consumers
        ],
        "meta": {
            "origin": "auto-detected",
            "canonical_value": value,
            "primary_kind": "identifier",
            "file_count": len({c[0] for c in consumers}),
            "hit_count": len(consumers),
        },
    }


# ---------------------------------------------------------------------------
# Empty cases
# ---------------------------------------------------------------------------

def test_no_changed_files_returns_empty_packet():
    units = [_fn_unit("a.py", "f1")]
    p = build_packet(changed_files=[], units=units)
    assert p.is_empty


def test_changed_files_no_matching_units():
    """Files changed but no unit anchored there → no changed_units."""
    p = build_packet(
        changed_files=["new_file.py"],
        units=[_fn_unit("other.py", "f1")],
    )
    assert p.changed_units == []
    assert p.related_couplings == []


# ---------------------------------------------------------------------------
# Anchor-based selection
# ---------------------------------------------------------------------------

def test_function_unit_anchored_to_changed_file():
    units = [
        _fn_unit("a.py", "f1"),
        _fn_unit("b.py", "f2"),
    ]
    p = build_packet(changed_files=["a.py"], units=units)
    assert len(p.changed_units) == 1
    assert p.changed_units[0]["id"] == "a.py::f1"


def test_template_unit_via_source_files():
    units = [_tpl_unit("templates/x.html", "main")]
    p = build_packet(changed_files=["templates/x.html"], units=units)
    assert len(p.changed_units) == 1
    assert p.changed_units[0]["id"].startswith("template:")


def test_function_units_listed_before_templates():
    """Stable ordering — fn_units first, then templates."""
    units = [
        _tpl_unit("a.py", "tpl"),  # weird but legitimate
        _fn_unit("a.py", "f1"),
    ]
    p = build_packet(changed_files=["a.py"], units=units)
    # The fn_unit anchored to a.py + the tpl_unit also referencing a.py:
    # both qualify, but fn first.
    assert p.changed_units[0]["kind"] == "function_unit"


# ---------------------------------------------------------------------------
# Coupling-based selection
# ---------------------------------------------------------------------------

def test_coupling_with_consumer_in_changed_file_included():
    cp = _coupling([("a.py", "f1"), ("b.py", "f2")])
    p = build_packet(changed_files=["a.py"], units=[cp])
    assert len(p.related_couplings) == 1


def test_coupling_with_no_consumers_in_changed_file_excluded():
    cp = _coupling([("c.py", "f3"), ("d.py", "f4")])
    p = build_packet(changed_files=["a.py"], units=[cp])
    assert p.related_couplings == []


def test_coupling_via_what_to_check_text_match():
    """Coupling whose what_to_check prose mentions the changed file path
    (but consumer.file is elsewhere) is still included — secondary text
    signal. Catches couplings where a JSON file is referenced in prose."""
    cp = {
        "id": "coupling:identifier:abc1",
        "consumers": [
            {"file": "engine/build.py", "anchor": "build_x",
             "what_to_check": "при правке data/tespy_topology.json — обновить здесь"},
        ],
        "meta": {"file_count": 1, "hit_count": 1, "primary_kind": "identifier"},
    }
    p = build_packet(
        changed_files=["data/tespy_topology.json"],
        units=[cp],
    )
    assert len(p.related_couplings) == 1


def test_coupling_text_match_falls_through_to_structural_match_first():
    """Structural match (consumer.file) is checked first; text-match
    only kicks in if structural fails. Avoids duplicate inclusion."""
    cp = {
        "id": "coupling:identifier:abc2",
        "consumers": [
            {"file": "data/tespy_topology.json", "anchor": "x",
             "what_to_check": "data/tespy_topology.json again here"},
        ],
        "meta": {"file_count": 1, "hit_count": 1, "primary_kind": "identifier"},
    }
    p = build_packet(
        changed_files=["data/tespy_topology.json"],
        units=[cp],
    )
    # Included via structural match (file). Not duplicated.
    assert len(p.related_couplings) == 1


def test_couplings_sorted_by_file_count_desc():
    """Most cross-cutting couplings come first — riskier drift surfaces."""
    cp_small = _coupling([("a.py", "f1"), ("b.py", "f2")], value="small")
    cp_big = _coupling([("a.py", "f1"), ("b.py", "f2"), ("c.py", "f3")], value="big")
    p = build_packet(
        changed_files=["a.py"],
        units=[cp_small, cp_big],
    )
    assert p.related_couplings[0]["meta"]["canonical_value"] == "big"


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

def test_changed_units_capped():
    """`_MAX_CHANGED_UNITS = 30` — test cap holds with 50 input."""
    units = [_fn_unit("a.py", f"f{i}") for i in range(50)]
    p = build_packet(changed_files=["a.py"], units=units)
    assert len(p.changed_units) == 30


def test_related_couplings_capped():
    """`_MAX_COUPLINGS = 40` — keep the most cross-cutting after cap."""
    units = [
        _coupling(
            [("a.py", f"f{i}"), ("b.py", f"g{i}")] + (
                [("c.py", f"h{i}")] if i < 10 else []
            ),
            value=f"v{i}",
        )
        for i in range(60)
    ]
    p = build_packet(changed_files=["a.py"], units=units)
    assert len(p.related_couplings) == 40
    # Top 10 should all have file_count=3 (the cross-cutting ones).
    assert all(c["meta"]["file_count"] == 3 for c in p.related_couplings[:10])


# ---------------------------------------------------------------------------
# Meta passthrough
# ---------------------------------------------------------------------------

def test_meta_passes_through():
    p = build_packet(
        changed_files=["a.py"],
        units=[_fn_unit("a.py", "f1")],
        meta={"base_commit": "abc123", "head_commit": "def456"},
    )
    assert p.meta["base_commit"] == "abc123"
    assert p.meta["head_commit"] == "def456"
