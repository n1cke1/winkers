"""Models for pre-computed impact analysis.

`impact.json` is the fourth file in the three-layer model (graph/semantic/rules
+ impact). It holds per-function LLM-assessed risk, caller classifications,
and refactoring guidance. Generated at `winkers init` time so scope()/orient()
can return it instantly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from winkers.models import FunctionNode

SCHEMA_VERSION = "2"  # v2 widens safe/dangerous_operations maxlen 15 → 100


class CallerClassification(BaseModel):
    caller: str                    # "filepath::name"
    dependency_type: str           # core_logic | proxy | fallback | logging | test
    coupling: str                  # tight | loose
    update_effort: str             # trivial | moderate | complex
    note: str = ""                 # 1-sentence rationale


class ImpactReport(BaseModel):
    """One function's pre-computed risk analysis."""
    content_hash: str
    risk_level: str                # low | medium | high | critical
    risk_score: float              # 0.0 — 1.0
    summary: str                   # 1-2 sentence description
    caller_classifications: list[CallerClassification] = []
    safe_operations: list[str] = []
    dangerous_operations: list[str] = []
    action_plan: str = ""


class ImpactMeta(BaseModel):
    generated_at: str = ""
    llm_model: str = ""
    functions_analyzed: int = 0
    functions_skipped: int = 0
    functions_failed: int = 0
    duration_seconds: float = 0.0


class ImpactFile(BaseModel):
    """Top-level impact.json schema."""
    schema_version: str = SCHEMA_VERSION
    meta: ImpactMeta = ImpactMeta()
    functions: dict[str, ImpactReport] = {}   # fn_id → ImpactReport


# ---------------------------------------------------------------------------
# Transient — never stored, just passed through the pipeline
# ---------------------------------------------------------------------------

@dataclass
class CallerInfo:
    """One caller of the target function, with source context for the prompt."""
    name: str                # "file::fn_name"
    filepath: str
    source: str              # full caller source (truncated if huge)
    call_context: str        # just the call line + a few surrounding


@dataclass
class FunctionContext:
    """Everything the LLM needs to analyze one function."""
    fn: FunctionNode
    source: str
    callers: list[CallerInfo] = field(default_factory=list)
    callees: list[str] = field(default_factory=list)   # fn_ids


@dataclass
class AnalysisResult:
    """Combined LLM output — intent + impact in one response.

    `primary_intent` → writes to FunctionNode.intent.
    `secondary_intents` → writes to FunctionNode.secondary_intents.
    The rest → writes to ImpactReport fields.
    """
    primary_intent: str
    secondary_intents: list[str]
    risk_level: str
    risk_score: float
    summary: str
    caller_classifications: list[CallerClassification]
    safe_operations: list[str]
    dangerous_operations: list[str]
    action_plan: str
