"""Impact analysis subpackage — per-function risk + intent pre-compute."""

from winkers.impact.generator import ImpactGenerator, load_impact_config
from winkers.impact.models import (
    AnalysisResult,
    CallerClassification,
    CallerInfo,
    FunctionContext,
    ImpactFile,
    ImpactMeta,
    ImpactReport,
)
from winkers.impact.store import ImpactStore

__all__ = [
    "AnalysisResult",
    "CallerClassification",
    "CallerInfo",
    "FunctionContext",
    "ImpactFile",
    "ImpactGenerator",
    "ImpactMeta",
    "ImpactReport",
    "ImpactStore",
    "load_impact_config",
]
