"""Tests for winkers.embeddings.builder — incremental encoding + search."""

import hashlib

import numpy as np
import pytest

import winkers.embeddings.builder as b


class _StubModel:
    """Deterministic stand-in: hash text → seeded random unit vector.

    Avoids loading BAAI/bge-m3 (~10s + 2GB). Same text → same vector,
    different text → different vector — sufficient for incremental
    logic tests.
    """

    def encode(self, texts, **kwargs):
        out = np.zeros((len(texts), b.DIMENSION), dtype=np.float32)
        for i, t in enumerate(texts):
            h = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
            np.random.seed(h % (2**32))
            v = np.random.randn(b.DIMENSION).astype(np.float32)
            v /= np.linalg.norm(v)
            out[i] = v
        return out


@pytest.fixture(autouse=True)
def stub_model(monkeypatch):
    monkeypatch.setattr(b, "_MODEL", _StubModel())
    yield


def _unit(uid: str, name: str = "", desc: str = "") -> dict:
    return {"id": uid, "name": name, "description": desc}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_load_missing_returns_empty(tmp_path):
    idx = b.load_index(tmp_path / "missing.npz")
    assert len(idx) == 0
    assert idx.vectors.shape == (0, b.DIMENSION)


def test_save_then_load_roundtrip(tmp_path):
    units = [_unit("u1", "A", "desc1"), _unit("u2", "B", "desc2")]
    idx, _ = b.embed_units(units)
    p = tmp_path / "embeddings.npz"
    b.save_index(idx, p)
    loaded = b.load_index(p)
    assert loaded.ids == idx.ids
    assert loaded.hashes == idx.hashes
    assert np.allclose(loaded.vectors, idx.vectors)


# ---------------------------------------------------------------------------
# Incremental encoding
# ---------------------------------------------------------------------------

def test_first_run_encodes_all(tmp_path):
    units = [_unit("u1", "A", "x"), _unit("u2", "B", "y")]
    idx, stats = b.embed_units(units)
    assert stats == {"reused": 0, "encoded": 2, "removed": 0}
    assert len(idx) == 2


def test_unchanged_units_reused(tmp_path):
    units = [_unit("u1", "A", "x"), _unit("u2", "B", "y")]
    idx1, _ = b.embed_units(units)
    idx2, stats = b.embed_units(units, existing=idx1)
    assert stats["reused"] == 2
    assert stats["encoded"] == 0
    # Vectors match (no re-randomization).
    assert np.allclose(idx2.vectors, idx1.vectors)


def test_changed_description_re_encoded():
    units1 = [_unit("u1", "A", "old")]
    idx1, _ = b.embed_units(units1)
    units2 = [_unit("u1", "A", "NEW")]
    idx2, stats = b.embed_units(units2, existing=idx1)
    assert stats == {"reused": 0, "encoded": 1, "removed": 0}
    assert not np.allclose(idx1.vectors[0], idx2.vectors[0])


def test_orphan_units_removed():
    units1 = [_unit("u1"), _unit("u2"), _unit("u3")]
    idx1, _ = b.embed_units(units1)
    units2 = [_unit("u1")]  # u2, u3 dropped
    idx2, stats = b.embed_units(units2, existing=idx1)
    assert stats["removed"] == 2
    assert idx2.ids == ["u1"]


def test_force_ignores_cache():
    units = [_unit("u1", "A", "x")]
    idx1, _ = b.embed_units(units)
    idx2, stats = b.embed_units(units, existing=idx1, force=True)
    assert stats == {"reused": 0, "encoded": 1, "removed": 0}


def test_unit_without_id_skipped():
    units = [_unit("u1", "A", "x"), {"name": "B", "description": "y"}]
    idx, stats = b.embed_units(units)
    # The id-less unit is skipped silently — no shape mismatch.
    assert idx.ids == ["u1"]


def test_embed_text_includes_summary_for_value_unit():
    """Wave 4b — value_unit has empty `description` but non-empty `summary`.
    The embed text should include the summary so BGE-M3 can match queries
    against the collection's value names."""
    unit = {
        "id": "value:status.py::VALID_STATUSES",
        "kind": "value_unit",
        "name": "VALID_STATUSES",
        "summary": "VALID_STATUSES: set of 3 value(s) ['draft', 'sent', 'paid']",
        "description": "",
    }
    text = b._embed_text_for(unit)
    assert "VALID_STATUSES" in text
    assert "'draft'" in text
    assert "'sent'" in text


def test_embed_text_uses_description_when_present():
    """Function units carry a real description — summary is optional."""
    unit = {
        "id": "fn1",
        "kind": "function_unit",
        "name": "calculate_price",
        "description": "Computes the total price of invoice line items.",
    }
    text = b._embed_text_for(unit)
    assert "calculate_price" in text
    assert "Computes the total" in text


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_returns_topk_sorted_desc():
    """Search returns top-k results sorted by cosine score desc.

    The stub model maps text → seeded random vector, so we can't assert
    *which* unit ranks first — that's the model's job, not search()'s.
    We assert the contract: result count, sort order, all hits valid.
    """
    units = [
        _unit("u1", "Apple", "fruit red"),
        _unit("u2", "Carrot", "vegetable orange"),
        _unit("u3", "Bicycle", "transport metal"),
    ]
    idx, _ = b.embed_units(units)
    results = b.search(idx, "Apple", k=2)
    assert len(results) == 2
    # All returned ids exist in the index.
    assert all(uid in idx.ids for _, uid in results)
    # Sorted by score desc.
    scores = [s for s, _ in results]
    assert scores == sorted(scores, reverse=True)


def test_search_query_repeated_returns_same_result():
    """Same query → same top result (deterministic stub)."""
    units = [_unit(f"u{i}", f"name{i}", f"desc{i}") for i in range(5)]
    idx, _ = b.embed_units(units)
    r1 = b.search(idx, "query", k=3)
    r2 = b.search(idx, "query", k=3)
    assert r1 == r2


def test_search_on_empty_index():
    empty = b.EmbeddingIndex(
        vectors=np.zeros((0, b.DIMENSION), dtype=np.float32),
        ids=[],
        hashes=[],
    )
    assert b.search(empty, "anything") == []


def test_save_index_atomic_via_tmp_file(tmp_path):
    """save_index goes through .tmp → rename so partial writes don't corrupt."""
    units = [_unit("u1", "A", "x")]
    idx, _ = b.embed_units(units)
    p = tmp_path / "x.npz"
    b.save_index(idx, p)
    assert p.exists()
    assert not (tmp_path / "x.npz.tmp").exists()  # tmp cleaned up
