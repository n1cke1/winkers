"""Tests for data file scanner + store integration."""

from __future__ import annotations

from pathlib import Path

from winkers.data_files.scanner import (
    MAX_FILE_BYTES,
    discover_data_files,
    read_data_file,
)
from winkers.descriptions.store import (
    UnitsStore,
    data_file_hash,
    section_hash,
)


def _write(p: Path, content: str = "{}") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# discover_data_files
# ---------------------------------------------------------------------------

def test_discover_finds_json_under_data_dir(tmp_path):
    _write(tmp_path / "data" / "config.json", '{"x": 1}')
    _write(tmp_path / "data" / "scenarios.yaml", "key: value")
    found = sorted(p.name for p in discover_data_files(tmp_path))
    assert found == ["config.json", "scenarios.yaml"]


def test_discover_skips_files_outside_data_dirs(tmp_path):
    _write(tmp_path / "data" / "kept.json")
    _write(tmp_path / "src" / "ignored.json")
    _write(tmp_path / "ignored.json")
    found = [p.name for p in discover_data_files(tmp_path)]
    assert "kept.json" in found
    assert "ignored.json" not in found


def test_discover_excludes_cache_files(tmp_path):
    _write(tmp_path / "data" / "tespy_formulas.json")
    _write(tmp_path / "data" / "calib_cache.json")
    _write(tmp_path / "data" / "build_cache.json")
    found = [p.name for p in discover_data_files(tmp_path)]
    assert "tespy_formulas.json" in found
    assert "calib_cache.json" not in found
    assert "build_cache.json" not in found


def test_discover_excludes_log_and_chat_files(tmp_path):
    _write(tmp_path / "data" / "access.log", "")
    _write(tmp_path / "data" / "userchat.json")
    _write(tmp_path / "data" / "topology.json")
    found = [p.name for p in discover_data_files(tmp_path)]
    assert found == ["topology.json"]


def test_discover_excludes_scenarios_dir(tmp_path):
    """data/scenarios/* — many auto-saved files. Skipped to avoid spam."""
    _write(tmp_path / "data" / "scenarios" / "20260401_winter.json")
    _write(tmp_path / "data" / "scenarios" / "20260402_summer.json")
    _write(tmp_path / "data" / "topology.json")
    found = [p.name for p in discover_data_files(tmp_path)]
    assert found == ["topology.json"]


def test_discover_excludes_hidden_dirs(tmp_path):
    """node_modules, __pycache__, .venv etc. are ignored."""
    for d in ("node_modules", "__pycache__", ".venv", ".git"):
        _write(tmp_path / "data" / d / "x.json")
    _write(tmp_path / "data" / "real.json")
    # `.winkers/` is also explicitly skipped
    _write(tmp_path / ".winkers" / "graph.json")
    found = [p.name for p in discover_data_files(tmp_path)]
    assert found == ["real.json"]


def test_discover_includes_config_dirs(tmp_path):
    """Both `data/` and `config/` are default include roots."""
    _write(tmp_path / "config" / "app.yaml")
    _write(tmp_path / "configs" / "deploy.yaml")
    found = sorted(p.name for p in discover_data_files(tmp_path))
    assert "app.yaml" in found
    assert "deploy.yaml" in found


def test_discover_custom_include_dirs(tmp_path):
    """Caller can override include_dirs to broaden / narrow scope."""
    _write(tmp_path / "data" / "default.json")
    _write(tmp_path / "fixtures" / "custom.json")
    found = sorted(
        p.name for p in discover_data_files(
            tmp_path, include_dirs=("fixtures",),
        )
    )
    assert found == ["custom.json"]


# ---------------------------------------------------------------------------
# read_data_file
# ---------------------------------------------------------------------------

def test_read_returns_entry_for_small_file(tmp_path):
    p = _write(tmp_path / "data" / "x.json", '{"a":1}')
    e = read_data_file(p, tmp_path)
    assert e is not None
    assert e.rel_path == "data/x.json"
    assert e.content == '{"a":1}'
    assert e.bytes_size == len('{"a":1}')


def test_read_skips_too_large_file(tmp_path):
    p = _write(tmp_path / "data" / "huge.json",
               "x" * (MAX_FILE_BYTES + 100))
    assert read_data_file(p, tmp_path) is None


def test_read_uses_forward_slash_relpath_on_windows(tmp_path):
    p = _write(tmp_path / "data" / "sub" / "x.json")
    e = read_data_file(p, tmp_path)
    assert e is not None
    assert "/" in e.rel_path
    assert "\\" not in e.rel_path


# ---------------------------------------------------------------------------
# UnitsStore.stale_data_file_units
# ---------------------------------------------------------------------------

class _FakeEntry:
    """Duck-types DataFileEntry for store staleness check."""
    def __init__(self, rel_path: str, content: str):
        self.rel_path = rel_path
        self.content = content


def test_stale_data_units_detects_new_file(tmp_path):
    store = UnitsStore(tmp_path)
    entry = _FakeEntry("data/x.json", '{"a":1}')
    stale = store.stale_data_file_units([], [entry])
    assert stale == {"data:data/x.json"}


def test_stale_data_units_detects_content_change(tmp_path):
    store = UnitsStore(tmp_path)
    existing = [
        {"id": "data:data/x.json",
         "source_hash": data_file_hash('{"a":1}')},
    ]
    entry = _FakeEntry("data/x.json", '{"a":2}')
    assert store.stale_data_file_units(existing, [entry]) == {
        "data:data/x.json",
    }


def test_stale_data_units_unchanged(tmp_path):
    store = UnitsStore(tmp_path)
    content = '{"a":1}'
    existing = [
        {"id": "data:data/x.json",
         "source_hash": data_file_hash(content)},
    ]
    entry = _FakeEntry("data/x.json", content)
    assert store.stale_data_file_units(existing, [entry]) == set()


def test_data_file_hash_matches_section_hash_implementation(tmp_path):
    """Both helpers wrap the same `_content_hash`. Different name,
    same algorithm."""
    s = "any content here"
    assert data_file_hash(s) == section_hash(s)


# ---------------------------------------------------------------------------
# UnitsStore.prune_orphans — data file orphans
# ---------------------------------------------------------------------------

def test_prune_drops_dead_data_units(tmp_path):
    store = UnitsStore(tmp_path)
    units = [
        {"id": "data:data/live.json"},
        {"id": "data:data/dead.json"},
    ]
    kept = store.prune_orphans(
        units,
        live_function_ids=set(),
        live_template_ids=set(),
        live_data_ids={"data:data/live.json"},
    )
    assert [u["id"] for u in kept] == ["data:data/live.json"]


def test_prune_keeps_data_units_when_live_data_ids_omitted(tmp_path):
    """Backward-compat: omitting live_data_ids preserves data units
    untouched (callers without data file flow shouldn't drop them)."""
    store = UnitsStore(tmp_path)
    units = [{"id": "data:data/x.json"}]
    kept = store.prune_orphans(
        units,
        live_function_ids=set(),
        live_template_ids=set(),
    )
    assert kept == units
