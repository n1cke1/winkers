"""BGE-M3 embedding builder + on-disk index.

Storage format (.winkers/embeddings.npz):
  vectors:  float32 array of shape (N, 1024)
  ids:      object array of N unit ids (str)
  hashes:   object array of N sha256(embed_text) — used for incremental skip

The model itself is loaded lazily on first encode; build of small
indices (≤50 units, e.g. one project at a time) is dominated by model
load (5–15s on CPU). Subsequent calls in the same process are warm.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-m3"
DIMENSION = 1024
INDEX_FILENAME = "embeddings.npz"
_BATCH_SIZE = 8


# Lazy-loaded singleton — initial load is 5-15s on CPU, so we keep it
# alive across calls within one process. Tests can patch `_get_model`
# to inject a stub. Lock guards against double-load when the MCP server
# preloads in a background thread and a request races in.
_MODEL = None
_MODEL_LOCK = threading.Lock()


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from sentence_transformers import SentenceTransformer
            log.info("Loading %s (one-time ~10s, then warm)", MODEL_NAME)
            t0 = time.monotonic()
            _MODEL = SentenceTransformer(MODEL_NAME)
            log.info("  loaded in %.1fs", time.monotonic() - t0)
    return _MODEL


def preload_model() -> None:
    """Trigger BGE-M3 load + a warmup encode. Safe from any thread.

    Loading weights warms the model object; the first encode additionally
    JITs the tokenizer and torch graph (~10s extra on CPU). We run a tiny
    encode here so the first real find_work_area query hits fully-warm
    paths. No-op if already warm.
    """
    model = _get_model()
    if getattr(model, "_winkers_warmed", False):
        return
    model.encode(
        ["warmup"],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    model._winkers_warmed = True


# ---------------------------------------------------------------------------
# Index data class + persistence
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingIndex:
    """In-memory representation of the embedding store.

    `vectors[i]` corresponds to `ids[i]` and was generated from text
    whose sha256 is `hashes[i]`. Re-embedding is needed only when
    sha256(embed_text) differs from the stored hash.
    """
    vectors: np.ndarray         # (N, DIMENSION) float32
    ids: list[str]
    hashes: list[str]

    def __len__(self) -> int:
        return len(self.ids)

    def hash_for(self, unit_id: str) -> str | None:
        """Return cached hash for an id, or None if not in index."""
        try:
            i = self.ids.index(unit_id)
        except ValueError:
            return None
        return self.hashes[i]


def load_index(path: Path) -> EmbeddingIndex:
    """Read an index from disk; return empty index if file is missing."""
    if not path.exists():
        return EmbeddingIndex(
            vectors=np.zeros((0, DIMENSION), dtype=np.float32),
            ids=[],
            hashes=[],
        )
    npz = np.load(path, allow_pickle=True)
    return EmbeddingIndex(
        vectors=npz["vectors"].astype(np.float32),
        ids=[str(x) for x in npz["ids"]],
        hashes=[str(x) for x in npz.get("hashes", np.array([""] * len(npz["ids"])))],
    )


def save_index(index: EmbeddingIndex, path: Path) -> None:
    """Write the index to disk atomically.

    `np.savez(str_path, ...)` auto-appends `.npz` to the filename, which
    breaks tmp→final rename. Passing an open file object skips that
    auto-extension and lets us name the temp file ourselves.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(
            f,
            vectors=index.vectors.astype(np.float32),
            ids=np.array(index.ids, dtype=object),
            hashes=np.array(index.hashes, dtype=object),
        )
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Build / incremental update
# ---------------------------------------------------------------------------

def _embed_text_hash(text: str) -> str:
    """Stable hash of unit's embed_text — drives cache invalidation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embed_text_for(unit: dict) -> str:
    """Compose the text we actually embed.

    `name + description` gives stronger signal than description alone —
    short descriptions get the unit name as anchor; long descriptions
    benefit from the name as a tie-breaker.
    """
    name = unit.get("name", "")
    desc = unit.get("description", "")
    return f"{name}\n\n{desc}".strip()


def embed_units(
    units: list[dict],
    existing: EmbeddingIndex | None = None,
    force: bool = False,
) -> tuple[EmbeddingIndex, dict]:
    """Build (or incrementally update) an index for a list of units.

    Returns the new index plus a stats dict: ``{"reused": N, "encoded": N,
    "removed": N}``. Reused = found in existing index with matching hash.
    Encoded = newly embedded. Removed = present in existing but not in
    `units` (orphans pruned).

    `force=True` ignores the existing cache and re-embeds everything.
    Used by `--force` flag on `winkers init`.
    """
    if existing is None:
        existing = EmbeddingIndex(
            vectors=np.zeros((0, DIMENSION), dtype=np.float32),
            ids=[],
            hashes=[],
        )

    # Stage 1: figure out what to keep, what to encode.
    incoming_ids = []
    texts_to_encode = []
    indices_for_encoded: list[int] = []  # index in `incoming_ids` for each encode item
    new_hashes_in_order: list[str] = []
    keep_from_existing: list[int] = []   # indices into existing.vectors
    keep_dest_indices: list[int] = []    # destination index in new index

    reused = encoded = 0

    for i, unit in enumerate(units):
        uid = unit.get("id")
        if not uid:
            continue
        text = _embed_text_for(unit)
        h = _embed_text_hash(text)
        incoming_ids.append(uid)
        new_hashes_in_order.append(h)

        # Check if existing index has this id with matching hash
        if not force and existing.hash_for(uid) == h:
            j = existing.ids.index(uid)
            keep_from_existing.append(j)
            keep_dest_indices.append(i)
            reused += 1
        else:
            texts_to_encode.append(text)
            indices_for_encoded.append(i)
            encoded += 1

    # Stage 2: encode the diff.
    if texts_to_encode:
        model = _get_model()
        log.info("Embedding %d new/changed unit(s)...", len(texts_to_encode))
        new_vecs = model.encode(
            texts_to_encode,
            batch_size=_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype(np.float32)
    else:
        new_vecs = np.zeros((0, DIMENSION), dtype=np.float32)

    # Stage 3: assemble final array in unit order.
    vectors = np.zeros((len(incoming_ids), DIMENSION), dtype=np.float32)
    for src_j, dst_i in zip(keep_from_existing, keep_dest_indices):
        vectors[dst_i] = existing.vectors[src_j]
    for k, dst_i in enumerate(indices_for_encoded):
        vectors[dst_i] = new_vecs[k]

    # "removed" = ids that were in the old index but absent from the new
    # input list (orphans pruned). Different from "not reused" — a unit
    # whose hash changed is encoded, not removed.
    removed = len(set(existing.ids) - set(incoming_ids))
    return EmbeddingIndex(
        vectors=vectors,
        ids=incoming_ids,
        hashes=new_hashes_in_order,
    ), {"reused": reused, "encoded": encoded, "removed": removed}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    index: EmbeddingIndex,
    query: str,
    k: int = 5,
) -> list[tuple[float, str]]:
    """Return top-k matches as [(cosine_score, unit_id), ...] sorted desc.

    Vectors are stored normalized, so cosine similarity is just a dot
    product — no sqrt or division at query time. ~1ms on 39 units.
    """
    if len(index) == 0:
        return []
    model = _get_model()
    qv = model.encode(
        [query],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )[0]
    sims = index.vectors @ qv
    top = np.argsort(-sims)[:k]
    return [(float(sims[i]), index.ids[i]) for i in top]
