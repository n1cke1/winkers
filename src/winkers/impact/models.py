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

SCHEMA_VERSION = "3"  # v3 adds `description` + `hardcoded_artifacts` to the
                       # combined LLM output (Wave 4c-1) so the descriptions
                       # pipeline no longer needs a separate LLM call per fn.


class CallerClassification(BaseModel):
    caller: str                    # "filepath::name"
    dependency_type: str           # core_logic | proxy | fallback | logging | test
    coupling: str                  # tight | loose
    update_effort: str             # trivial | moderate | complex
    note: str = ""                 # 1-sentence rationale


class ImpactHardcodedArtifact(BaseModel):
    """One load-bearing literal value in the function. Mirrors the
    existing ``HardcodedArtifact`` from ``winkers.descriptions.models``
    but lives here so impact.json can be parsed without importing the
    descriptions package (avoids cycles)."""
    value: str | list[str]
    # kind: count | identifier | id_list | phrase | threshold | route | other
    kind: str
    context: str
    surface: str | None = None


class ImpactReport(BaseModel):
    """One function's pre-computed risk analysis + embedding-grade description.

    Wave 4c-1 added `description` and `hardcoded_artifacts` so the
    units pipeline pulls function-unit description text from here
    rather than spawning a second LLM call. Both are optional —
    pre-v3 reports loaded from disk simply omit them.
    """
    content_hash: str
    risk_level: str                # low | medium | high | critical
    risk_score: float              # 0.0 — 1.0
    summary: str                   # 1-2 sentence description (short, used as `intent` field)
    description: str = ""          # 70-120w prose for embedding index; "" until v3
    hardcoded_artifacts: list[ImpactHardcodedArtifact] = []  # load-bearing literals; [] until v3
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
    """Combined LLM output — intent + impact + description in one response.

    Wave 4c-1: also carries the embedding-grade `description` and
    `hardcoded_artifacts`. Field provenance:

    - `primary_intent` → ``FunctionNode.intent`` (short tag).
    - `secondary_intents` → ``FunctionNode.secondary_intents``.
    - `description` → ``units.json::function_unit.description`` (paragraph).
    - `hardcoded_artifacts` → ``units.json::function_unit.hardcoded_artifacts``.
    - The rest → ``ImpactReport`` fields.

    Both `description` and `hardcoded_artifacts` default to empty so
    older single-response parsers (or LLM responses missing these
    fields) keep working — the units pipeline falls back to its
    standalone description-author call when description comes back blank.
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
    description: str = ""
    hardcoded_artifacts: list[ImpactHardcodedArtifact] = field(default_factory=list)
