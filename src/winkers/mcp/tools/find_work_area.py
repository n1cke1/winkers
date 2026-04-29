"""MCP tool: find_work_area — semantic search over per-unit LLM descriptions."""

from __future__ import annotations

from pathlib import Path

from mcp.types import Tool

from winkers.models import Graph

TOOL = Tool(
    name="find_work_area",
    description=(
        "DEPRECATED — use orient(task=...) instead. orient now"
        " always returns semantic_matches against the registered"
        " task. find_work_area is kept as an alias for one minor"
        " for existing scripts/agents and will be removed."
        " Locate where in the codebase to make a change."
        " Describe the task in 1-2 sentences in any language —"
        " plain prose, mixing Russian and English domain terms"
        " is fine."
        " Returns top-K relevant function_units and"
        " traceability_units (UI sections, cross-file couplings)"
        " with confidence verdict."
        " On verdict='OK': top match is the place to start."
        " On verdict='NO_CLEAR_MATCH': no existing unit fits well"
        " — likely a new feature, or the query uses domain"
        " vocabulary missing from the index."
        " Requires `winkers init --with-units` to have run; falls"
        " back to an error message otherwise."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What you want to do, in natural language."
                    " Examples: 'добавить переключатель темы',"
                    " 'fix negative condensate in SLP loop',"
                    " 'where does the IDX dict live'."
                ),
            },
            "k": {
                "type": "integer",
                "description": "Top-K matches to return (default 5)",
            },
        },
        "required": ["query"],
    },
)

# Adaptive threshold (validated on CHP tickets in Phase 0 spike). Hard
# 0.55 catches confident matches; 0.45 floor + score gap salvages
# borderline cases where top-1 is correct but the absolute score is low.
_THRESHOLD_HARD = 0.55
_THRESHOLD_FLOOR = 0.45
_THRESHOLD_GAP = 0.05


_FIND_WORK_AREA_TOOL_NAME = "find_work_area"


def _tool_find_work_area(graph: Graph, args: dict, root: Path) -> dict:
    """Search the description-first units index for relevant code/UI.

    The actual heavy lifting (BGE-M3 model load + cosine top-K) is in
    `winkers.embeddings.builder.search`. This wrapper handles index
    presence, joins with graph for line numbers, applies the adaptive
    threshold, and shapes a JSON response for the agent.
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}
    k = int(args.get("k", 5))
    if k < 1:
        k = 5

    from winkers.descriptions.store import UnitsStore
    from winkers.embeddings import (
        INDEX_FILENAME,
        load_index,
        preload_status,
        search,
        wait_for_preload,
    )

    idx_path = root / ".winkers" / INDEX_FILENAME
    if not idx_path.exists():
        return {
            "error": "Units index not built yet.",
            "hint": (
                "Run `winkers init --with-units` to author per-unit "
                "descriptions and build embeddings. Without the index, "
                "fall back to `before_create` for fuzzy graph search."
            ),
        }

    # If a background preload is in flight, calling search() now would
    # block on the model lock for tens of seconds. Wait up to 15s for
    # warmup to finish — agents ignored the prior "retry shortly" hint
    # and never came back, so a bounded synchronous wait is preferable
    # to returning empty-handed. 15s is well under MCP's per-tool
    # timeout (~60-120s) and typically rides out the last few seconds
    # of preload.
    status = preload_status()
    if status.get("state") == "loading":
        if not wait_for_preload(timeout=15.0):
            elapsed = preload_status().get("elapsed_s", 0.0)
            return {
                "warming": True,
                "elapsed_s": elapsed,
                "hint": (
                    f"BGE-M3 still warming after {elapsed}s (waited 15s here). "
                    "Use orient/browse/before_create now and retry "
                    "find_work_area afterwards — it will be fast."
                ),
            }

    index = load_index(idx_path)
    if len(index) == 0:
        return {
            "error": "Units index empty.",
            "hint": "Run `winkers init --with-units --force-units`.",
        }

    units_by_id = {u["id"]: u for u in UnitsStore(root).load()}
    raw = search(index, query, k=max(k, 5))
    if not raw:
        return {
            "matches": [],
            "max_score": 0.0,
            "verdict": "EMPTY",
            "advice": (
                "No vectors in index — rebuild via "
                "`winkers init --with-units --force-units`."
            ),
        }

    # Wave 7 — register a fresh tool-call slot for context dedup. Every
    # description emitted below either (a) gets suppressed because a
    # prior tool call within the threshold already showed it, or
    # (b) gets recorded so a subsequent call can suppress.
    from winkers.session.seen_units import SeenUnitsRegistry
    seen_registry = SeenUnitsRegistry.get()
    call_idx = seen_registry.begin_call(_FIND_WORK_AREA_TOOL_NAME)
    fresh_emit_ids: list[str] = []

    top_score = raw[0][0]
    bottom_score = raw[-1][0]
    gap = top_score - bottom_score

    if top_score >= _THRESHOLD_HARD:
        verdict, confidence = "OK", "high"
    elif top_score >= _THRESHOLD_FLOOR and gap >= _THRESHOLD_GAP:
        verdict, confidence = "OK", "medium"
    else:
        verdict, confidence = "NO_CLEAR_MATCH", "low"

    matches: list[dict] = []
    for score, uid in raw[:k]:
        unit = units_by_id.get(uid)
        if unit is None:
            # Embeddings index has an id units.json doesn't — stale link;
            # surface it but don't pretend we have details.
            matches.append({
                "id": uid,
                "kind": "unknown",
                "score": round(score, 3),
                "warning": "Vector exists but unit missing from units.json — re-run init.",
            })
            continue

        item: dict = {
            "id": uid,
            "kind": unit.get("kind", "unknown"),
            "score": round(score, 3),
            "name": unit.get("name", uid),
        }

        # function_unit gets line numbers from graph
        if unit.get("kind") == "function_unit":
            fn = graph.functions.get(uid)
            if fn is not None:
                item["file"] = fn.file
                item["line_start"] = fn.line_start
                item["line_end"] = fn.line_end
                if getattr(fn, "route", None):
                    item["route"] = (
                        f"{fn.http_method or 'GET'} {fn.route}"
                    )
        elif unit.get("kind") == "value_unit":
            # value_unit anchors at the collection's defining line.
            # Surface values + consumer counts directly so the agent
            # doesn't need a follow-up scope() to see the blast radius.
            anchor = unit.get("anchor") or {}
            if anchor.get("file"):
                item["file"] = anchor["file"]
            if anchor.get("line"):
                item["line"] = anchor["line"]
            values = unit.get("values") or []
            if values:
                item["values"] = values
            count = unit.get("consumer_count")
            if count is not None:
                item["consumer_count"] = count
            consumer_files = unit.get("consumer_files") or []
            if consumer_files:
                item["consumer_files"] = consumer_files
        else:
            # traceability_unit: source_files (always) + source_anchors
            # (when LLM extracted fn-level anchors) — enables drill-down.
            sf = unit.get("source_files") or []
            if sf:
                item["source_files"] = sf
            anchors = unit.get("source_anchors") or []
            if anchors:
                # If anchors are graph fn_ids, attach line ranges so the
                # agent can jump straight to the named function.
                resolved = []
                for a in anchors:
                    fn = graph.functions.get(a)
                    if fn is not None:
                        resolved.append({
                            "id": a,
                            "file": fn.file,
                            "line_start": fn.line_start,
                            "line_end": fn.line_end,
                        })
                    else:
                        resolved.append({"id": a})
                item["source_anchors"] = resolved

        # Trim description for tool output — full text is in units.json
        # if the agent wants it. 250 chars is enough for intent recognition.
        desc = unit.get("description") or ""
        if len(desc) > 250:
            desc = desc[:247] + "..."
        if desc:
            item["description"] = desc
        # value_unit's description is empty until Wave 4c — use the
        # structural summary instead so the agent has something useful.
        elif unit.get("kind") == "value_unit":
            summary = unit.get("summary") or ""
            if summary:
                item["summary"] = summary

        # Wave 7 — context dedup. If this unit was shown with a full
        # description by a prior tool call inside the threshold window,
        # swap `description` for a `description_seen_in` marker so the
        # agent doesn't re-read the heavy paragraph.
        if "description" in item:
            seen_registry.maybe_suppress_description(uid, item)
            if "description" in item:
                fresh_emit_ids.append(uid)

        matches.append(item)

    # Record the IDs whose full description we just emitted. Repeat
    # calls within the threshold see them as "recently seen" and get
    # the suppression marker.
    seen_registry.record(fresh_emit_ids, _FIND_WORK_AREA_TOOL_NAME, call_idx)

    advice = _find_work_area_advice(verdict, top_score)
    return {
        "matches": matches,
        "max_score": round(top_score, 3),
        "confidence": confidence,
        "verdict": verdict,
        "advice": advice,
    }


def _find_work_area_advice(verdict: str, max_score: float) -> str:
    if verdict == "OK":
        return (
            "Top match is likely the right place. Open at line_start (for "
            "function_unit) or via source_anchors (for traceability_unit) "
            "and verify intent before editing. If multiple top hits look "
            "equally relevant, run `scope` on each for caller context."
        )
    return (
        f"No strongly-matching unit (max_score={max_score:.2f}). Two "
        "interpretations: (1) genuinely a new feature with no existing "
        "implementation — write fresh code; (2) the query uses domain "
        "vocabulary missing from descriptions — try rephrasing with "
        "code-side terms (function names, module names you already know). "
        "Fall back to `before_create` for fuzzy graph match if needed."
    )
