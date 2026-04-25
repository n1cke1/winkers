"""Embed CHP units via BGE-M3 (local, no API).

Reads scripts/units.json (39 units), loads BAAI/bge-m3 via
sentence-transformers, encodes each unit's `embed_text` (name +
description), and saves vectors + id list to scripts/embeddings.npz.

Run once after units.json changes. Subsequent runs are quick — model
weights are cached on disk by HuggingFace under ~/.cache/huggingface.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).parent
UNITS_PATH = ROOT / "units.json"
OUT_PATH = ROOT / "embeddings.npz"

# BGE-M3: multilingual (RU+EN+code), 1024-dim, supports up to 8192 tokens.
MODEL_NAME = "BAAI/bge-m3"


def main() -> None:
    units = json.loads(UNITS_PATH.read_text(encoding="utf-8"))["units"]
    texts = [u["embed_text"] for u in units]
    ids = [u["id"] for u in units]
    kinds = [u["kind"] for u in units]

    print(f"Loading {MODEL_NAME} (first run downloads ~2.3 GB)...")
    t0 = time.monotonic()
    model = SentenceTransformer(MODEL_NAME)
    print(f"  loaded in {time.monotonic() - t0:.1f}s")

    print(f"Encoding {len(texts)} units (batch)...")
    t0 = time.monotonic()
    # normalize_embeddings=True → unit-norm vectors → cosine = dot product
    vectors = model.encode(
        texts,
        batch_size=8,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    encode_s = time.monotonic() - t0
    print(f"  encoded in {encode_s:.1f}s ({encode_s / len(texts) * 1000:.0f} ms/unit avg)")
    print(f"  vector shape: {vectors.shape}, dtype: {vectors.dtype}")

    np.savez(
        OUT_PATH,
        vectors=vectors.astype(np.float32),
        ids=np.array(ids, dtype=object),
        kinds=np.array(kinds, dtype=object),
    )
    print(f"Saved to {OUT_PATH}  ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
