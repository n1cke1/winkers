"""ImpactStore — load / save / prune .winkers/impact.json."""

from __future__ import annotations

import json
from pathlib import Path

from winkers.impact.models import ImpactFile
from winkers.store import STORE_DIR

IMPACT_FILE = "impact.json"


class ImpactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store_dir = root / STORE_DIR
        self.path = self.store_dir / IMPACT_FILE

    def load(self) -> ImpactFile:
        """Load impact.json, or return empty ImpactFile if missing/corrupt."""
        if not self.path.exists():
            return ImpactFile()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ImpactFile.model_validate(data)
        except Exception:
            return ImpactFile()

    def save(self, impact: ImpactFile) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            impact.model_dump_json(indent=2, exclude_defaults=False),
            encoding="utf-8",
        )

    def exists(self) -> bool:
        return self.path.exists()

    @staticmethod
    def prune(impact: ImpactFile, live_fn_ids: set[str]) -> int:
        """Drop entries for functions that no longer exist. Returns count removed."""
        stale = [fid for fid in impact.functions if fid not in live_fn_ids]
        for fid in stale:
            del impact.functions[fid]
        return len(stale)
