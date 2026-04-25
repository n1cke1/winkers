"""Tests for winkers.descriptions.store — UnitsStore."""

from dataclasses import dataclass

from winkers.descriptions.store import UnitsStore, section_hash


@dataclass
class _Section:
    """Stand-in for templates.scanner.TemplateSection — duck-typed in store."""
    file: str
    id: str
    content: str


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_load_missing_returns_empty(tmp_path):
    store = UnitsStore(tmp_path)
    assert store.load() == []


def test_load_malformed_returns_empty(tmp_path):
    store = UnitsStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() == []


def test_save_then_load_roundtrip(tmp_path):
    store = UnitsStore(tmp_path)
    units = [
        {"id": "a", "description": "x"},
        {"id": "b", "description": "y"},
    ]
    store.save(units)
    loaded = store.load()
    assert sorted(u["id"] for u in loaded) == ["a", "b"]


def test_save_is_id_sorted(tmp_path):
    """Stable ordering — diffs between init runs stay readable."""
    store = UnitsStore(tmp_path)
    store.save([
        {"id": "z"}, {"id": "a"}, {"id": "m"},
    ])
    loaded = store.load()
    assert [u["id"] for u in loaded] == ["a", "m", "z"]


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def test_upsert_adds_new(tmp_path):
    store = UnitsStore(tmp_path)
    units = [{"id": "a"}]
    after = store.upsert(units, {"id": "b"})
    assert sorted(u["id"] for u in after) == ["a", "b"]


def test_upsert_replaces_same_id(tmp_path):
    store = UnitsStore(tmp_path)
    units = [{"id": "a", "v": 1}]
    after = store.upsert(units, {"id": "a", "v": 2})
    assert len(after) == 1
    assert after[0]["v"] == 2


def test_upsert_skips_unit_without_id(tmp_path):
    store = UnitsStore(tmp_path)
    units = [{"id": "a"}]
    # No-id unit is dropped; original list unchanged.
    after = store.upsert(units, {"description": "x"})
    assert after == units


# ---------------------------------------------------------------------------
# Staleness — function_unit
# ---------------------------------------------------------------------------

def test_stale_fn_units_detects_new(tmp_path):
    store = UnitsStore(tmp_path)
    existing = []  # nothing described yet
    graph = {"a.py::f": {"ast_hash": "ABC"}}
    stale = store.stale_function_units(existing, graph)
    assert stale == {"a.py::f"}


def test_stale_fn_units_detects_changed_hash(tmp_path):
    store = UnitsStore(tmp_path)
    existing = [
        {"id": "a.py::f", "kind": "function_unit", "source_hash": "OLD"},
    ]
    graph = {"a.py::f": {"ast_hash": "NEW"}}
    stale = store.stale_function_units(existing, graph)
    assert stale == {"a.py::f"}


def test_stale_fn_units_skips_matching_hash(tmp_path):
    store = UnitsStore(tmp_path)
    existing = [
        {"id": "a.py::f", "kind": "function_unit", "source_hash": "ABC"},
    ]
    graph = {"a.py::f": {"ast_hash": "ABC"}}
    assert store.stale_function_units(existing, graph) == set()


def test_stale_fn_units_ignores_when_graph_lacks_hash(tmp_path):
    """Older graphs without ast_hash → don't churn descriptions."""
    store = UnitsStore(tmp_path)
    graph = {"a.py::f": {"ast_hash": None}}
    assert store.stale_function_units([], graph) == set()


# ---------------------------------------------------------------------------
# Staleness — template
# ---------------------------------------------------------------------------

def test_stale_template_units_detects_new_section(tmp_path):
    store = UnitsStore(tmp_path)
    sections = [_Section("t.html", "main", "<div>content</div>")]
    stale = store.stale_template_units([], sections)
    assert stale == {"template:t.html#main"}


def test_stale_template_units_detects_content_change(tmp_path):
    store = UnitsStore(tmp_path)
    existing = [
        {"id": "template:t.html#main",
         "source_hash": section_hash("<div>old</div>")},
    ]
    sections = [_Section("t.html", "main", "<div>NEW</div>")]
    assert store.stale_template_units(existing, sections) == {
        "template:t.html#main",
    }


def test_stale_template_units_unchanged(tmp_path):
    store = UnitsStore(tmp_path)
    content = "<div>same</div>"
    existing = [
        {"id": "template:t.html#main",
         "source_hash": section_hash(content)},
    ]
    sections = [_Section("t.html", "main", content)]
    assert store.stale_template_units(existing, sections) == set()


# ---------------------------------------------------------------------------
# Orphan pruning
# ---------------------------------------------------------------------------

def test_prune_drops_dead_function_units(tmp_path):
    store = UnitsStore(tmp_path)
    units = [
        {"id": "a.py::live", "kind": "function_unit"},
        {"id": "a.py::dead", "kind": "function_unit"},
    ]
    kept = store.prune_orphans(
        units, live_function_ids={"a.py::live"}, live_template_ids=set(),
    )
    assert [u["id"] for u in kept] == ["a.py::live"]


def test_prune_drops_dead_template_units(tmp_path):
    store = UnitsStore(tmp_path)
    units = [
        {"id": "template:t.html#live"},
        {"id": "template:t.html#dead"},
    ]
    kept = store.prune_orphans(
        units, live_function_ids=set(),
        live_template_ids={"template:t.html#live"},
    )
    assert [u["id"] for u in kept] == ["template:t.html#live"]


def test_prune_keeps_manual_traceability_units(tmp_path):
    """Manual concept units always survive — orphan logic is per-kind."""
    store = UnitsStore(tmp_path)
    units = [
        {"id": "manual_concept_1", "kind": "traceability_unit"},
        {"id": "coupling:count:abc", "kind": "traceability_unit",
         "meta": {"origin": "auto-detected"}},
    ]
    kept = store.prune_orphans(
        units, live_function_ids=set(), live_template_ids=set(),
    )
    assert sorted(u["id"] for u in kept) == [
        "coupling:count:abc",
        "manual_concept_1",
    ]


def test_section_hash_stability():
    """Same content → same hash; one-char change → different hash."""
    a = section_hash("<div>X</div>")
    b = section_hash("<div>X</div>")
    c = section_hash("<div>Y</div>")
    assert a == b
    assert a != c
