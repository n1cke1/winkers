"""Session analyzer -- sends session trace to Haiku to find knowledge gaps."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import BaseModel

from winkers.models import ScoredSession

ANALYZE_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
You are analyzing an AI coding agent's work session.
Find knowledge gaps -- what the agent didn't know that cost time or quality.
For each gap, write the EXACT TEXT that should be added to the project's
semantic layer (semantic.json) so the NEXT agent session has this knowledge
from the start.

All knowledge is delivered through semantic.json -> CLAUDE.md prompt.
NOT through MCP tools. The agent reads semantic.json before starting work.

Categories:
NAVIGATION -- spent turns finding what one call could answer
CONSTRAINT -- business rule unknown until failure
CONVENTION -- code works but breaks project patterns
LOCATION -- didn't know where code belongs
TOOL_DESCRIPTION -- MCP tool existed but description didn't trigger use
DEBT -- change works but architecture degraded (complexity/coupling grew)

Where in semantic.json:
CONSTRAINT -> constraints[] (add new constraint with "why")
CONVENTION -> conventions[] (add rule + wrong_approach)
LOCATION -> zone_intents{} or monster_files{}
NAVIGATION -> conventions[] (add hint about using scope())
TOOL_DESCRIPTION -> tool_descriptions (update tool wording)
DEBT -> conventions[] (add architectural rule)

Rules:
- Only gaps that ACTUALLY cost turns or quality in THIS session
- injection_content must be concise (1-2 sentences max)
- injection_content is what the agent should READ before working, \
not a description of the problem
- DEBT gaps: always report if complexity_delta > 10
- Empty array if session was efficient

Output: JSON array only, no markdown fences."""

OUTPUT_SCHEMA = """\
[
  {
    "category": "NAVIGATION|CONSTRAINT|CONVENTION|LOCATION|TOOL_DESCRIPTION|DEBT",
    "description": "what happened in this session",
    "turns_affected": [3, 4, 5],
    "turns_wasted": 3,
    "tokens_wasted": 5000,
    "semantic_target": "constraints|conventions|zone_intents|monster_files|tool_descriptions",
    "injection_content": "exact text to add to semantic.json",
    "priority": "high|medium|low"
  }
]"""


class Insight(BaseModel):
    category: str
    description: str
    turns_affected: list[int] = []
    turns_wasted: int = 0
    tokens_wasted: int = 0
    semantic_target: str = ""
    injection_content: str = ""
    priority: str = "low"
    session_id: str = ""


class AnalysisResult(BaseModel):
    session_id: str
    insights: list[Insight] = []
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0


def _build_user_message(
    scored: ScoredSession, semantic_json: str,
) -> str:
    """Build the user message for the Haiku analysis prompt."""
    session = scored.session
    debt = scored.debt

    # Compact session trace: task, tool calls, corrections
    trace_parts = [
        f"Task: {session.task_prompt}",
        f"Model: {session.model}",
        f"Turns: {session.total_turns} "
        f"(exploration={session.exploration_turns}, "
        f"modification={session.modification_turns}, "
        f"verification={session.verification_turns})",
        f"Session end: {session.session_end}",
        f"Tests passed: {session.tests_passed}",
    ]

    if session.winkers_calls:
        trace_parts.append(f"Winkers MCP calls: {session.winkers_calls}")

    if session.user_corrections:
        trace_parts.append(
            "User corrections:\n"
            + "\n".join(f"  - {c}" for c in session.user_corrections)
        )

    # Tool call summary (compact)
    trace_parts.append("\nTool calls:")
    for i, tc in enumerate(session.tool_calls):
        params_summary = _summarize_params(tc.input_params)
        trace_parts.append(f"  [{i+1}] {tc.name}({params_summary})")

    trace_parts.append(f"\nFiles read: {session.files_read}")
    trace_parts.append(f"Files modified: {session.files_modified}")
    trace_parts.append(f"Files created: {session.files_created}")

    session_text = "\n".join(trace_parts)

    debt_text = (
        f"complexity_delta: {debt.complexity_delta}, "
        f"max_function_lines: {debt.max_function_lines}, "
        f"biggest_file_growth: {debt.biggest_file_growth}, "
        f"import_edges_delta: {debt.import_edges_delta}, "
        f"files_created: {debt.files_created}, "
        f"files_modified: {debt.files_modified}"
    )

    return (
        f"## Session Trace\n{session_text}\n\n"
        f"## Current Semantic Layer\n{semantic_json}\n\n"
        f"## Technical Debt Delta\n{debt_text}\n\n"
        f"## Approval Score\n{scored.score:.2f}\n\n"
        f"## Output Schema\n{OUTPUT_SCHEMA}"
    )


def _summarize_params(params: dict) -> str:
    """One-line summary of tool call params."""
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def analyze_session(
    scored: ScoredSession, semantic_json: str,
    api_key: str | None = None,
) -> AnalysisResult:
    """Send session to Haiku for knowledge gap analysis."""
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "Analysis requires the 'anthropic' package. "
            "Install with: pip install anthropic"
        )

    from winkers.semantic import _build_http_client

    http_client = _build_http_client()
    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if http_client:
        kwargs["http_client"] = http_client
    client = anthropic.Anthropic(**kwargs)

    model = os.environ.get("WINKERS_ANALYZE_MODEL", ANALYZE_MODEL)
    user_msg = _build_user_message(scored, semantic_json)

    start = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text
        if text.strip().startswith("```"):
            text = text.strip().split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()
        raw = json.loads(text)
    except Exception as e:
        raise RuntimeError(f"Analysis failed: {e}") from e

    elapsed = time.monotonic() - start
    usage = getattr(response, "usage", None)

    insights = []
    for item in raw:
        insight = Insight(
            category=item.get("category", ""),
            description=item.get("description", ""),
            turns_affected=item.get("turns_affected", []),
            turns_wasted=item.get("turns_wasted", 0),
            tokens_wasted=item.get("tokens_wasted", 0),
            semantic_target=item.get("semantic_target", ""),
            injection_content=item.get("injection_content", ""),
            priority=item.get("priority", "low"),
            session_id=scored.session.session_id,
        )
        insights.append(insight)

    # Force DEBT insight if complexity_delta > 10 and not already present
    if scored.debt.complexity_delta > 10:
        has_debt = any(i.category == "DEBT" for i in insights)
        if not has_debt:
            insights.append(Insight(
                category="DEBT",
                description=(
                    f"Complexity grew by {scored.debt.complexity_delta} "
                    f"in this session"
                ),
                turns_wasted=0,
                semantic_target="conventions",
                injection_content=(
                    "Keep cyclomatic complexity stable. "
                    "Extract complex logic into separate functions."
                ),
                priority="high",
                session_id=scored.session.session_id,
            ))

    # Instant high priority for debt gaps with low score
    if scored.score < 0.4:
        for i in insights:
            if i.category == "DEBT":
                i.priority = "high"

    return AnalysisResult(
        session_id=scored.session.session_id,
        insights=insights,
        model=model,
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        duration_s=round(elapsed, 1),
    )
