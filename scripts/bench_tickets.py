"""Bench the embedding index against 6 real CHP tickets.

For each ticket we hand-label which units in the index are relevant
(they would help an agent solve the task). Then run the actual search
and compute recall@5 + precision@5 + threshold behaviour.

The hand labels are conservative — they include any unit whose code
or concept the actual ticket touched, per the wip markdown.
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
THRESHOLD = 0.55           # absolute floor for "clear match"
ADAPTIVE_FLOOR = 0.45      # if max_score in [0.45, 0.55) AND has clear leader, accept
ADAPTIVE_GAP = 0.05        # required gap between top-1 and top-5 to qualify


# Ground truth derived from data/wip/T-*.md content.
# Each entry: realistic intent the user would have written + the units that
# the actual change touched, derived from the wip "Что сделано" log.
TICKETS = [
    {
        "id": "T-132CDB",
        "intent_kind": "analytical",  # no code change, just explanation
        "query": "Можно ли обеспечить 147 т/ч на ПВД с одной PT1? Сколько пара подаётся на турбину в режиме лето 4 котла",
        "relevant": {
            "linear_coeffs",
            "engine/chp_network.py::extract_coefficients",
            "chp_results_structure",
            "engine/chp_network.py::get_results",
            "engine/turbine_factory.py::build_pt_turbine",
        },
    },
    {
        "id": "T-598600",
        "intent_kind": "bug_fix",
        "query": "При активных Р-турбинах конденсат уходит в минус, MILP не сходится — добавить каскадный дренаж ПНД через valve и per-turbine K_PVD_eff",
        "relevant": {
            "slp_cycle",
            "engine/chp_model.py::_cond_violations",
            "engine/chp_network.py::extract_coefficients",
            "engine/chp_model.py::solve_design",
            "linear_coeffs",
            "engine/turbine_factory.py::build_pt_turbine",
            "engine/chp_network.py::_build",
            "engine/equations.py::build_constraints",
            "ui_tab_tespy",
            "ui_tab_scheme",
            "chp_results_structure",
            "svg_schema",
            "milp_constraints",
        },
    },
    {
        "id": "T-987E79",
        "intent_kind": "research",
        "query": "Где в коде описан баланс пара — какие ограничения и где они",
        "relevant": {
            "engine/equations.py::build_constraints",
            "milp_constraints",
            "engine/linear_model.py::calibrate",
            "linear_coeffs",
            "engine/unified_solver.py::solve_milp",
        },
    },
    {
        "id": "T-C7FC95",
        "intent_kind": "new_feature",  # mostly new code, expect low overlap
        "query": "Добавь переключение темы день/ночь в интерфейс с кнопкой в топбаре",
        "relevant": {
            "ui_tab_app_shell",
            "approach_tab_static",  # templates/index.html — куда добавить link/script
        },
    },
    {
        "id": "T-F696C8",
        "intent_kind": "refactor_plus_bug_fix",
        "query": "Переименовать K_PVD в K_regen во всём коде и исправить отрицательный конденсат через SLP-цикл",
        "relevant": {
            "linear_coeffs",
            "slp_cycle",
            "engine/chp_network.py::extract_coefficients",
            "engine/chp_model.py::_cond_violations",
            "engine/chp_model.py::solve_design",
            "engine/equations.py::build_constraints",
            "formula_catalog",
            "engine/linear_model.py::calibrate",
            "approach_tab_static",
            "ui_tab_tespy",
        },
    },
    {
        "id": "T-FC3170",
        "intent_kind": "topology_change",
        "query": "Добавить промышленный коллектор 13 ата для PT1 и PT2 промышленных отборов",
        "relevant": {
            "topology",
            "engine/chp_network.py::_build",
            "ui_tab_tespy",
        },
    },
]


# ---------------------------------------------------------------------------
# Search + metrics
# ---------------------------------------------------------------------------

def search(model, query: str, vectors: np.ndarray, ids: np.ndarray, k: int = 5):
    qv = model.encode(
        [query], normalize_embeddings=True, convert_to_numpy=True,
        show_progress_bar=False,
    )[0]
    sims = vectors @ qv
    top_idx = np.argsort(-sims)[:k]
    return [(float(sims[i]), str(ids[i])) for i in top_idx]


def recall_at_k(top: list[tuple[float, str]], relevant: set[str], k: int = 5) -> float:
    hits = sum(1 for _, uid in top[:k] if uid in relevant)
    if not relevant:
        return float("nan")
    return hits / len(relevant)


def precision_at_k(top: list[tuple[float, str]], relevant: set[str], k: int = 5) -> float:
    hits = sum(1 for _, uid in top[:k] if uid in relevant)
    return hits / k


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    units = json.loads(UNITS_PATH.read_text(encoding="utf-8"))["units"]
    by_id = {u["id"]: u for u in units}

    npz = np.load(EMBEDDINGS_PATH, allow_pickle=True)
    vectors = npz["vectors"]
    ids = npz["ids"]
    print(f"Loaded {len(ids)} unit vectors, dim={vectors.shape[1]}")

    t0 = time.monotonic()
    model = SentenceTransformer(MODEL_NAME)
    print(f"Model loaded in {time.monotonic() - t0:.1f}s")
    print()

    # Validate ground-truth ids exist in index
    all_ids = set(str(i) for i in ids)
    for t in TICKETS:
        bogus = t["relevant"] - all_ids
        if bogus:
            print(f"⚠ {t['id']}: ground-truth ids not in index: {bogus}")

    # Run all queries
    summaries = []
    for t in TICKETS:
        q = t["query"]
        rel = t["relevant"]
        top = search(model, q, vectors, ids, k=5)
        max_score = top[0][0] if top else 0.0

        rec5 = recall_at_k(top, rel, k=5)
        prec5 = precision_at_k(top, rel, k=5)

        # Hard threshold (baseline)
        hard_pass = max_score >= THRESHOLD

        # Adaptive threshold: accept if max_score in [ADAPTIVE_FLOOR, THRESHOLD)
        # but the gap between top-1 and top-5 is large (clear leader signal).
        gap = max_score - top[-1][0] if len(top) >= 5 else 0.0
        adaptive_pass = (
            hard_pass
            or (max_score >= ADAPTIVE_FLOOR and gap >= ADAPTIVE_GAP)
        )

        summaries.append({
            "id": t["id"],
            "kind": t["intent_kind"],
            "max_score": max_score,
            "top5_gap": gap,
            "hard_pass": hard_pass,
            "adaptive_pass": adaptive_pass,
            "above_threshold": adaptive_pass,  # used for legacy aggregate
            "recall@5": rec5,
            "precision@5": prec5,
            "relevant_count": len(rel),
            "hits_in_top5": int(rec5 * len(rel)),
        })

        print(f"=== {t['id']} ({t['intent_kind']}) ===")
        print(f"    query: {q[:90]}{'…' if len(q) > 90 else ''}")
        hard = "OK" if hard_pass else "BELOW"
        adapt = "OK" if adaptive_pass else "BELOW"
        print(f"    max_score={max_score:.3f} (gap={gap:.3f})  "
              f"hard={hard}  adaptive={adapt}  "
              f"recall@5={rec5:.2f}  precision@5={prec5:.2f}  "
              f"({summaries[-1]['hits_in_top5']}/{len(rel)} relevant in top-5)")
        for score, uid in top:
            mark = "✓" if uid in rel else " "
            name = by_id[uid].get("name", uid)
            print(f"      {mark} {score:.3f}  {uid:55s}  {name[:40]}")
        print()

    # Aggregate
    print("=" * 70)
    print("AGGREGATE")
    print("=" * 70)
    valid_recalls = [s["recall@5"] for s in summaries if s["kind"] != "new_feature"]
    print(f"  mean recall@5    (excl. new_feature):      {sum(valid_recalls) / len(valid_recalls):.3f}")
    valid_precs = [s["precision@5"] for s in summaries if s["kind"] != "new_feature"]
    print(f"  mean precision@5 (excl. new_feature):      {sum(valid_precs) / len(valid_precs):.3f}")
    hard_correct = 0
    adaptive_correct = 0
    for s in summaries:
        expect_above = s["kind"] != "new_feature"
        if s["hard_pass"] == expect_above:
            hard_correct += 1
        if s["adaptive_pass"] == expect_above:
            adaptive_correct += 1
    print(f"  hard threshold correct:     {hard_correct}/{len(summaries)}")
    print(f"  adaptive threshold correct: {adaptive_correct}/{len(summaries)}")
    print()
    print("  Per-ticket recall@5:")
    for s in summaries:
        marker = "  " if s["above_threshold"] == (s["kind"] != "new_feature") else "⚠ "
        print(f"    {marker}{s['id']}  recall={s['recall@5']:.2f}  "
              f"max_score={s['max_score']:.3f}  ({s['kind']})")


if __name__ == "__main__":
    main()
