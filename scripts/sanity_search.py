"""Sanity-check the BGE-M3 index with a handful of natural-language queries.

For each query: embed via the same model, compute cosine sim against all
39 unit vectors, print top-5 matches. No HyDE, no RRF — pure embedding
baseline. This is the "honest baseline" we'll compare against later.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).parent
UNITS_PATH = ROOT / "units.json"
EMBEDDINGS_PATH = ROOT / "embeddings.npz"
MODEL_NAME = "BAAI/bge-m3"

# Queries chosen to probe coverage breadth:
#  - python core (MILP, SLP, calibration)
#  - JS UI tabs
#  - cross-cutting concepts from ui_traceability
#  - one query that should NOT match anything well (auth/2FA — excluded zone)
QUERIES = [
    "как устроен SLP-цикл сходимости",
    "где обновить счётчик переменных в Подходе",
    "куда добавить новую целевую функцию для оптимизатора",
    "отрисовка таблицы помесячных результатов",
    "как переключить активную вкладку",
    "применение сценария к конфигурации",
    "linearization coefficients calibration from TESPy",
    "live SVG diagram update on calculation finish",
    "AI chat panel that creates tickets",
    "where to add 2FA auth login",  # should ideally score low across the board
]


def cosine_topk(query_vec: np.ndarray, vectors: np.ndarray, k: int = 5):
    """Return [(score, idx), ...] sorted desc — vectors and query are unit-norm."""
    sims = vectors @ query_vec
    top_idx = np.argsort(-sims)[:k]
    return [(float(sims[i]), int(i)) for i in top_idx]


def main() -> None:
    units = json.loads(UNITS_PATH.read_text(encoding="utf-8"))["units"]
    by_id = {u["id"]: u for u in units}

    npz = np.load(EMBEDDINGS_PATH, allow_pickle=True)
    vectors = npz["vectors"]
    ids = npz["ids"]
    kinds = npz["kinds"]
    print(f"Loaded {len(ids)} vectors, dim={vectors.shape[1]}")

    t0 = time.monotonic()
    model = SentenceTransformer(MODEL_NAME)
    print(f"Model loaded in {time.monotonic() - t0:.1f}s")

    THRESHOLD = 0.55  # below this → "no clear match"

    for q in QUERIES:
        t0 = time.monotonic()
        qv = model.encode(
            [q],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        embed_ms = (time.monotonic() - t0) * 1000
        top = cosine_topk(qv, vectors, k=5)

        print(f"\n=== «{q}»  (embed {embed_ms:.0f}ms)")
        max_score = top[0][0]
        verdict = "OK" if max_score >= THRESHOLD else f"BELOW THRESHOLD ({THRESHOLD})"
        print(f"    max_score={max_score:.3f}  -> {verdict}")
        for score, idx in top:
            uid = str(ids[idx])
            kind = str(kinds[idx])
            name = by_id[uid].get("name", uid)
            print(f"    {score:.3f}  [{kind:18s}] {uid:48s}  {name}")


if __name__ == "__main__":
    main()
