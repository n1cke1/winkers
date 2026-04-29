"""BGE-M3 embedding builder + on-disk index.

Storage format (.winkers/embeddings.npz):
  vectors:  float32 array of shape (N, 1024)
  ids:      object array of N unit ids (str)
  hashes:   object array of N sha256(embed_text) — used for incremental skip

The model is BGE-M3 served as ONNX INT8 (Xenova/bge-m3). Loaded lazily
on first encode (~2-3s cold on CPU; was 10-15s with sentence-transformers
float32). Resident RAM ~1.1 GiB (vs 1.7 GiB float32). Set
WINKERS_USE_LEGACY_ST=1 to fall back to sentence-transformers float32.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-m3"           # logical model id (vectors are interchangeable)
_ONNX_REPO = "Xenova/bge-m3"         # actual ONNX-INT8 source on HF
_ONNX_FILE = "onnx/sentence_transformers_int8.onnx"
_ONNX_ALLOW_PATTERNS = [
    _ONNX_FILE,
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
    "sentencepiece.bpe.model",
]
DIMENSION = 1024
INDEX_FILENAME = "embeddings.npz"
_BATCH_SIZE = 8
_MAX_LENGTH = 512


class _OnnxBgeM3:
    """ONNX-INT8 BGE-M3 wrapped with a `.encode()` API compatible with
    sentence_transformers.SentenceTransformer — drop-in replacement for
    the rest of this module. The ONNX graph (sentence_transformers_int8.onnx)
    has CLS-pooling and L2-normalize built in, so output vectors are already
    1024-dim L2-normalized float32; pooling/normalize args are accepted-and-
    ignored.
    """

    def __init__(self) -> None:
        import onnxruntime as ort
        from huggingface_hub import snapshot_download
        from tokenizers import Tokenizer

        snap = snapshot_download(
            repo_id=_ONNX_REPO,
            allow_patterns=_ONNX_ALLOW_PATTERNS,
        )
        self._tok = Tokenizer.from_file(f"{snap}/tokenizer.json")
        self._tok.enable_padding()
        self._tok.enable_truncation(max_length=_MAX_LENGTH)
        self._sess = ort.InferenceSession(
            f"{snap}/{_ONNX_FILE}",
            providers=["CPUExecutionProvider"],
        )
        # Used by preload_model() to skip a redundant warmup encode.
        self._winkers_warmed = False

    def encode(
        self,
        texts,
        batch_size: int = _BATCH_SIZE,
        normalize_embeddings: bool = True,   # accepted-and-ignored: ONNX graph normalizes
        convert_to_numpy: bool = True,        # accepted-and-ignored: always numpy
        show_progress_bar: bool = False,      # accepted-and-ignored
    ) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.zeros((0, DIMENSION), dtype=np.float32)
        chunks: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            encs = self._tok.encode_batch(batch)
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            emb = self._sess.run(
                ["sentence_embedding"],
                {"input_ids": ids, "attention_mask": mask},
            )[0]
            chunks.append(emb.astype(np.float32, copy=False))
        return np.concatenate(chunks, axis=0) if len(chunks) > 1 else chunks[0]


# Lazy-loaded singleton — initial load is 2-3s on CPU (ONNX-INT8) or 10-15s
# (legacy sentence-transformers float32), so we keep it alive across calls
# within one process. Tests can patch `_get_model` to inject a stub. Lock
# guards against double-load when the MCP server preloads in a background
# thread and a request races in.
_MODEL = None
_MODEL_LOCK = threading.Lock()

# Preload state — lets `_tool_find_work_area` distinguish "no preload
# started, lazy-load on demand (slow but expected)" from "background
# preload running, query would block on lock for tens of seconds —
# return early so the agent doesn't hit the MCP per-tool timeout".
_PRELOAD_LOCK = threading.Lock()
_PRELOAD_STARTED_AT: float | None = None
_PRELOAD_DONE_AT: float | None = None
_PRELOAD_DONE_EVENT = threading.Event()


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            t0 = time.monotonic()
            if os.environ.get("WINKERS_USE_LEGACY_ST") == "1":
                from sentence_transformers import SentenceTransformer
                log.info("Loading %s via sentence-transformers (LEGACY float32)", MODEL_NAME)
                _MODEL = SentenceTransformer(MODEL_NAME)
            else:
                log.info("Loading %s ONNX-INT8 (one-time ~3s, then warm)", _ONNX_REPO)
                _MODEL = _OnnxBgeM3()
            log.info("  loaded in %.1fs", time.monotonic() - t0)
    return _MODEL


def preload_model() -> None:
    """Trigger BGE-M3 load + a warmup encode. Safe from any thread.

    Loading weights warms the model object; the first encode additionally
    JITs the tokenizer and torch graph (~10s extra on CPU). We run a tiny
    encode here so the first real find_work_area query hits fully-warm
    paths. No-op if already warm.
    """
    global _PRELOAD_STARTED_AT, _PRELOAD_DONE_AT
    with _PRELOAD_LOCK:
        if _PRELOAD_DONE_AT is not None:
            return
        if _PRELOAD_STARTED_AT is None:
            _PRELOAD_STARTED_AT = time.monotonic()
    model = _get_model()
    if not getattr(model, "_winkers_warmed", False):
        model.encode(
            ["warmup"],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        model._winkers_warmed = True
    with _PRELOAD_LOCK:
        _PRELOAD_DONE_AT = time.monotonic()
    _PRELOAD_DONE_EVENT.set()


def wait_for_preload(timeout: float) -> bool:
    """Block up to `timeout` seconds waiting for an in-flight preload.

    Returns True if the model is (or becomes) ready before the deadline,
    False on timeout. Used by `_tool_find_work_area` to ride out the
    last few seconds of warmup instead of returning early — keeps the
    wait well under MCP's per-tool timeout (~60-120s).
    """
    return _PRELOAD_DONE_EVENT.wait(timeout=timeout)


def preload_status() -> dict:
    """Return current preload state for callers that want to avoid blocking.

    States:
      - "idle":    no preload kicked off; on-demand load will block (expected).
      - "loading": background preload in flight; querying now would block on
                   _MODEL_LOCK for tens of seconds — caller should defer.
      - "ready":   model + warmup encode complete; queries are fast.
    """
    with _PRELOAD_LOCK:
        if _PRELOAD_DONE_AT is not None:
            return {"state": "ready"}
        if _PRELOAD_STARTED_AT is None:
            return {"state": "idle"}
        return {
            "state": "loading",
            "elapsed_s": round(time.monotonic() - _PRELOAD_STARTED_AT, 1),
        }


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

    `name + summary + description` gives stronger signal than description
    alone — short descriptions get the unit name as anchor; long
    descriptions benefit from the name as a tie-breaker. `summary` is a
    short structural blurb authored by `build_value_units` (Wave 4b)
    that surfaces the actual collection values, giving BGE-M3 something
    to match against before LLM-authored descriptions land in Wave 4c.
    """
    name = unit.get("name", "")
    summary = unit.get("summary", "")
    desc = unit.get("description", "")
    return "\n\n".join(s for s in (name, summary, desc) if s).strip()


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
