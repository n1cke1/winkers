"""Accumulate and merge knowledge gap insights."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from winkers.analyzer import AnalysisResult, Insight
from winkers.store import STORE_DIR

INSIGHTS_FILE = "insights.json"
SIMILARITY_THRESHOLD = 0.8


class StoredInsight(Insight):
    occurrences: int = 1
    session_ids: list[str] = []
    status: str = "open"  # open | fixed


class InsightsStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / STORE_DIR / INSIGHTS_FILE

    def load(self) -> list[StoredInsight]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [StoredInsight.model_validate(item) for item in raw]
        except Exception:
            return []

    def save(self, insights: list[StoredInsight]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [i.model_dump() for i in insights]
        self.path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )

    def merge(self, result: AnalysisResult) -> list[StoredInsight]:
        """Merge new analysis insights into accumulated store."""
        existing = self.load()

        for new_insight in result.insights:
            match = _find_similar(new_insight, existing)
            if match:
                _merge_into(match, new_insight)
            else:
                existing.append(_to_stored(new_insight))

        self.save(existing)
        return existing

    def open_insights(self) -> list[StoredInsight]:
        """Return only unfixed insights, sorted by priority."""
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(
            [i for i in self.load() if i.status == "open"],
            key=lambda i: (order.get(i.priority, 3), -i.occurrences),
        )

    def mark_fixed(self, indices: list[int]) -> None:
        """Mark insights at given indices as fixed."""
        all_insights = self.load()
        open_list = [i for i in all_insights if i.status == "open"]
        for idx in indices:
            if 0 <= idx < len(open_list):
                open_list[idx].status = "fixed"
        self.save(all_insights)


def _find_similar(
    new: Insight, existing: list[StoredInsight],
) -> StoredInsight | None:
    """Find an existing insight similar enough to merge."""
    for item in existing:
        if item.status == "fixed":
            continue
        if item.semantic_target != new.semantic_target:
            continue
        desc_sim = _similarity(item.description, new.description)
        content_sim = _similarity(
            item.injection_content, new.injection_content,
        )
        if desc_sim > SIMILARITY_THRESHOLD or content_sim > SIMILARITY_THRESHOLD:
            return item
    return None


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _merge_into(existing: StoredInsight, new: Insight) -> None:
    """Merge a new insight into an existing one."""
    existing.occurrences += 1
    if new.session_id and new.session_id not in existing.session_ids:
        existing.session_ids.append(new.session_id)
    existing.turns_wasted += new.turns_wasted
    existing.tokens_wasted += new.tokens_wasted
    # Escalate priority based on occurrences
    if existing.occurrences >= 3:
        existing.priority = "high"
    elif existing.occurrences >= 2:
        existing.priority = "medium"


def _to_stored(insight: Insight) -> StoredInsight:
    """Convert a new Insight to a StoredInsight."""
    return StoredInsight(
        category=insight.category,
        description=insight.description,
        turns_affected=insight.turns_affected,
        turns_wasted=insight.turns_wasted,
        tokens_wasted=insight.tokens_wasted,
        semantic_target=insight.semantic_target,
        injection_content=insight.injection_content,
        priority=insight.priority,
        session_id=insight.session_id,
        session_ids=[insight.session_id] if insight.session_id else [],
        occurrences=1,
        status="open",
    )
