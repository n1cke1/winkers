"""ImpactStore — shim that reads / writes impact data through ``units.json``.

Wave 4d retired the standalone ``.winkers/impact.json`` file. Per-function
risk analysis now lives inside ``units.json::function_unit`` records,
alongside the description / hardcoded_artifacts that the same combined
LLM call produces. This module preserves the ``ImpactStore`` /
``ImpactFile`` API so the ~10 consumers (mcp/tools.py, dashboard,
generator, CLI status print) keep working without churn.

On first load when units.json has no impact fields but a legacy
impact.json sits on disk, the shim does a one-shot migration: the
legacy file is parsed, fields are folded into the matching
function_units, units.json is saved, and the loaded ImpactFile is
returned to the caller. The legacy file is left in place (read-only)
so a rollback to a prior winkers doesn't lose state.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from winkers.descriptions.store import UnitsStore
from winkers.impact.models import (
    SCHEMA_VERSION,
    CallerClassification,
    ImpactFile,
    ImpactHardcodedArtifact,
    ImpactMeta,
    ImpactReport,
)
from winkers.store import STORE_DIR

log = logging.getLogger(__name__)

LEGACY_IMPACT_FILE = "impact.json"

# Field set persisted on the function_unit dict. Keep the names lining up
# with ImpactReport so round-trips are mechanical.
_IMPACT_FIELDS = (
    "content_hash",
    "risk_level",
    "risk_score",
    "summary",
    "caller_classifications",
    "safe_operations",
    "dangerous_operations",
    "action_plan",
    "description",
    "hardcoded_artifacts",
)


class ImpactStore:
    """Backwards-compat ``ImpactFile`` view over ``units.json``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store_dir = root / STORE_DIR
        # Kept for callers that still touch `.path` directly (legacy probes).
        self.path = self.store_dir / LEGACY_IMPACT_FILE
        self._units = UnitsStore(root)

    # ------------------------------------------------------------------
    # Public API (unchanged from the pre-4d ImpactStore)
    # ------------------------------------------------------------------

    def load(self) -> ImpactFile:
        """Build an ImpactFile from units.json. Migrates legacy on first call.

        Files with an outdated schema_version (legacy or units-side) are
        treated as missing so the next generator run refills them from
        scratch — prevents pre-v3 data leaking forward.
        """
        meta = self._load_meta()
        functions = self._load_functions()

        if not functions:
            # Fall back to legacy impact.json once. If it has data we
            # haven't migrated yet, fold it into units.json now.
            migrated = self._migrate_from_legacy()
            if migrated is not None:
                return migrated

        return ImpactFile(meta=meta, functions=functions)

    def save(self, impact: ImpactFile) -> None:
        """Persist by merging impact fields into units.json function_units.

        New entries (no matching function_unit) get a stub unit so a
        future run finds them — the description authoring step will
        upgrade the stub when it has graph context.
        """
        units = self._units.load()
        by_id = {u.get("id"): u for u in units if u.get("id")}

        for fn_id, report in impact.functions.items():
            unit = by_id.get(fn_id)
            if unit is None:
                # Stub a function_unit so the impact data has a home.
                # The unit pipeline will fill name/anchor next time.
                unit = {"id": fn_id, "kind": "function_unit"}
                by_id[fn_id] = unit
                units.append(unit)
            _write_report_to_unit(unit, report)

        self._units.save(units)
        self._units.save_impact_meta(_meta_to_dict(impact.meta))

    def exists(self) -> bool:
        """True if either units.json carries impact data or legacy file is on disk."""
        if any(_unit_has_impact(u) for u in self._units.load()):
            return True
        return self.path.exists()

    @staticmethod
    def prune(impact: ImpactFile, live_fn_ids: set[str]) -> int:
        """Drop entries for functions that no longer exist. Returns count removed."""
        stale = [fid for fid in impact.functions if fid not in live_fn_ids]
        for fid in stale:
            del impact.functions[fid]
        return len(stale)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_functions(self) -> dict[str, ImpactReport]:
        out: dict[str, ImpactReport] = {}
        for unit in self._units.load():
            if unit.get("kind") != "function_unit":
                continue
            fn_id = unit.get("id")
            if not fn_id:
                continue
            report = _read_report_from_unit(unit)
            if report is not None:
                out[fn_id] = report
        return out

    def _load_meta(self) -> ImpactMeta:
        raw = self._units.load_impact_meta()
        if not raw:
            return ImpactMeta()
        try:
            return ImpactMeta.model_validate(raw)
        except Exception:
            return ImpactMeta()

    def _migrate_from_legacy(self) -> ImpactFile | None:
        """One-shot: read legacy impact.json, fold into units.json.

        Returns the same ImpactFile that subsequent loads will produce.
        The legacy file is NOT deleted — kept for rollback safety,
        same approach as semantic.json after Wave 4a.
        """
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("schema_version") != SCHEMA_VERSION:
            return None
        try:
            legacy = ImpactFile.model_validate(data)
        except Exception:
            return None

        if not legacy.functions:
            # Save the meta even when there are no function entries —
            # so subsequent loads stop hitting the migration path.
            self._units.save_impact_meta(_meta_to_dict(legacy.meta))
            return legacy

        log.info(
            "impact: migrating %d function(s) from legacy impact.json",
            len(legacy.functions),
        )
        self.save(legacy)
        return legacy


# ---------------------------------------------------------------------------
# Helpers — function_unit dict ↔ ImpactReport conversion
# ---------------------------------------------------------------------------

def _unit_has_impact(unit: dict) -> bool:
    if unit.get("kind") != "function_unit":
        return False
    return bool(unit.get("risk_level") and unit.get("content_hash"))


def _read_report_from_unit(unit: dict) -> ImpactReport | None:
    if not _unit_has_impact(unit):
        return None
    try:
        return ImpactReport(
            content_hash=str(unit.get("content_hash", "")),
            risk_level=str(unit.get("risk_level", "low")),
            risk_score=float(unit.get("risk_score", 0.0)),
            summary=str(unit.get("summary", "")),
            description=str(unit.get("description", "")),
            hardcoded_artifacts=[
                ImpactHardcodedArtifact.model_validate(a)
                for a in unit.get("hardcoded_artifacts", [])
                if isinstance(a, dict)
            ],
            caller_classifications=[
                CallerClassification.model_validate(c)
                for c in unit.get("caller_classifications", [])
                if isinstance(c, dict)
            ],
            safe_operations=list(unit.get("safe_operations", [])),
            dangerous_operations=list(unit.get("dangerous_operations", [])),
            action_plan=str(unit.get("action_plan", "")),
        )
    except (TypeError, ValueError):
        return None


def _write_report_to_unit(unit: dict, report: ImpactReport) -> None:
    """Update only impact-owned fields on `unit`; leaves description/artifacts
    overlapping with the units pipeline alone unless the report has values."""
    unit["content_hash"] = report.content_hash
    unit["risk_level"] = report.risk_level
    unit["risk_score"] = report.risk_score
    unit["summary"] = report.summary
    unit["caller_classifications"] = [
        c.model_dump() for c in report.caller_classifications
    ]
    unit["safe_operations"] = list(report.safe_operations)
    unit["dangerous_operations"] = list(report.dangerous_operations)
    unit["action_plan"] = report.action_plan
    if report.description:
        unit["description"] = report.description
    if report.hardcoded_artifacts:
        unit["hardcoded_artifacts"] = [
            a.model_dump(exclude_none=True) for a in report.hardcoded_artifacts
        ]


def _meta_to_dict(meta: ImpactMeta) -> dict:
    return meta.model_dump()
