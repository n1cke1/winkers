"""Combined prompt (intent + impact) + JSON response validation.

One LLM call per function produces both:
- `primary_intent` + `secondary_intents` → stored on FunctionNode
- `risk_level` + `caller_classifications` + ... → stored in ImpactFile

Running intent-only and impact-only as separate calls would double API cost
for no benefit (same input context both times).
"""

from __future__ import annotations

import json
import logging
import re

from winkers.impact.models import AnalysisResult, CallerClassification, FunctionContext

log = logging.getLogger(__name__)


_VALID_RISK = {"low", "medium", "high", "critical"}
_VALID_DEPENDENCY = {"core_logic", "proxy", "fallback", "logging", "test"}
_VALID_COUPLING = {"tight", "loose"}
_VALID_EFFORT = {"trivial", "moderate", "complex"}


COMBINED_PROMPT = """You are a senior code reviewer. Analyze this function and its callers.

Produce BOTH:
1. an intent breakdown (primary + secondary sub-tasks inside the function body)
2. a risk assessment for modifying this function

FUNCTION ({filepath}):
```{language}
{function_source}
```

CALLERS ({n_shown} of {n_total}, most coupled shown):
{callers_block}

Respond in JSON ONLY, no markdown fences. Use this exact schema:
{{
  "primary_intent": "main purpose, 1 short phrase",
  "secondary_intents": ["concrete sub-tasks: 'email validation', 'password hashing', ..."],
  "risk_level": "low|medium|high|critical",
  "risk_score": 0.0,
  "summary": "what the function does, 1-2 sentences",
  "caller_classifications": [
    {{
      "caller": "filepath::name",
      "dependency_type": "core_logic|proxy|fallback|logging|test",
      "coupling": "tight|loose",
      "update_effort": "trivial|moderate|complex",
      "note": "1 sentence rationale"
    }}
  ],
  "safe_operations": ["rename", "add optional param", ...],
  "dangerous_operations": ["change return type", ...],
  "action_plan": "concrete refactoring steps, 2-3 sentences"
}}

Rules:
- secondary_intents: only real logic. Skip boilerplate like "logging" or "error handling".
- Use standard terms so same logic in different functions gets the same tag.
- caller_classifications: one entry per shown caller above, same "filepath::name" format.
- risk_score between 0.0 and 1.0; align with risk_level.
"""


def build_prompt(ctx: FunctionContext, max_callers: int = 10) -> str:
    """Render the combined prompt for one function context."""
    shown = ctx.callers[:max_callers]
    callers_block = "\n\n".join(_format_caller(c, ctx.fn.language) for c in shown) \
        or "(no callers — leaf function)"
    body = _truncate_source(ctx.source, max_lines=200)

    return COMBINED_PROMPT.format(
        filepath=ctx.fn.file,
        language=ctx.fn.language,
        function_source=body,
        n_shown=len(shown),
        n_total=len(ctx.callers),
        callers_block=callers_block,
    )


def parse_response(raw: str) -> AnalysisResult | None:
    """Parse and validate a combined-analysis JSON response. Returns None if invalid."""
    data = _extract_json(raw)
    if data is None:
        log.debug("analysis: no JSON object in response")
        return None

    try:
        return _validate(data)
    except (ValueError, TypeError, KeyError) as e:
        log.debug("analysis: validation failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Find the first top-level JSON object in `text`. Tolerates markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        fence_end = text.rfind("```")
        if fence_end > 3:
            text = text[text.find("\n") + 1: fence_end].strip()
    # Find balanced braces
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _validate(data: dict) -> AnalysisResult:
    primary = _str(data, "primary_intent", maxlen=200).strip()
    if not primary:
        raise ValueError("primary_intent is empty")

    secondary_raw = data.get("secondary_intents", [])
    if not isinstance(secondary_raw, list):
        raise ValueError("secondary_intents must be a list")
    secondary = [
        _clean_tag(s) for s in secondary_raw
        if isinstance(s, str) and _clean_tag(s)
    ][:10]

    risk_level = _str(data, "risk_level").lower()
    if risk_level not in _VALID_RISK:
        raise ValueError(f"risk_level must be one of {_VALID_RISK}, got {risk_level!r}")

    score = data.get("risk_score", 0.0)
    try:
        risk_score = float(score)
    except (TypeError, ValueError):
        raise ValueError("risk_score must be numeric") from None
    risk_score = max(0.0, min(1.0, risk_score))

    summary = _str(data, "summary", maxlen=400).strip()

    ccs_raw = data.get("caller_classifications", [])
    if not isinstance(ccs_raw, list):
        ccs_raw = []
    ccs: list[CallerClassification] = []
    for entry in ccs_raw:
        if not isinstance(entry, dict):
            continue
        try:
            dep = str(entry.get("dependency_type", "")).lower()
            coup = str(entry.get("coupling", "")).lower()
            eff = str(entry.get("update_effort", "")).lower()
            if dep not in _VALID_DEPENDENCY:
                continue
            if coup not in _VALID_COUPLING:
                continue
            if eff not in _VALID_EFFORT:
                continue
            ccs.append(CallerClassification(
                caller=str(entry.get("caller", ""))[:200],
                dependency_type=dep,
                coupling=coup,
                update_effort=eff,
                note=str(entry.get("note", ""))[:300],
            ))
        except (TypeError, ValueError):
            continue

    safe_ops = _str_list(data.get("safe_operations", []), maxlen=15)
    dangerous_ops = _str_list(data.get("dangerous_operations", []), maxlen=15)
    action_plan = _str(data, "action_plan", maxlen=600).strip()

    return AnalysisResult(
        primary_intent=primary,
        secondary_intents=secondary,
        risk_level=risk_level,
        risk_score=risk_score,
        summary=summary,
        caller_classifications=ccs,
        safe_operations=safe_ops,
        dangerous_operations=dangerous_ops,
        action_plan=action_plan,
    )


def _str(data: dict, key: str, maxlen: int = 200) -> str:
    v = data.get(key, "")
    if not isinstance(v, str):
        return ""
    if len(v) > maxlen:
        return v[:maxlen]
    return v


def _clean_tag(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s[:60]


def _str_list(raw, maxlen: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned[:maxlen])
    return out[:10]


def _format_caller(caller, language: str) -> str:
    return (
        f"--- {caller.name} ({caller.filepath}) ---\n"
        f"```{language}\n{caller.call_context}\n```"
    )


def _truncate_source(source: str, max_lines: int) -> str:
    lines = source.splitlines()
    if len(lines) <= max_lines:
        return source
    head = lines[: max_lines // 2]
    tail = lines[-max_lines // 2:]
    return "\n".join(head + [f"# ... ({len(lines) - max_lines} lines elided) ..."] + tail)
