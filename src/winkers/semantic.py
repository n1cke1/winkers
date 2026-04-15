"""Semantic layer — architectural context that cannot be computed from code structure."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from winkers.conventions import (
    DismissedFile,
    ProposedRule,
    RuleAdd,
    RuleRemove,
    RulesAudit,
    RuleUpdate,
)
from winkers.models import Graph
from winkers.store import STORE_DIR

SEMANTIC_FILE = "semantic.json"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
Read this project's source code and dependency graph.
Then create semantic.json.

This file will be read by an AI coding agent BEFORE it writes any code.
The agent ALREADY HAS the dependency graph (callers, imports, complexity).
Do NOT repeat what the graph shows.

Write ONLY what the agent cannot figure out from code structure alone.
Target: 1-2KB JSON. Every sentence must be actionable.

### RULES

1. NO OBVIOUS THINGS. If any competent developer would know it from
   reading the code for 5 minutes — don't write it. "Database queries
   go through sql_access.py" is visible from imports. Skip it.

2. NO TECHNICAL TRIVIA. "Decimal must be converted to float for numpy"
   is a Python fact, not a project insight. Skip it.

3. SPECIFIC AFFECTS. Never write affects: ["root"]. Name exact files.
   "affects carbon_calc.py lines 45-80" is useful.
   "affects root" is useless.

4. EXPLAIN DOMAIN. If the project uses domain-specific concepts,
   write one sentence explaining what it means FOR THE CODE.
   Not a textbook definition — how it affects what the agent should
   and should not do.

5. DATA FLOW FIRST. The most important thing: how data moves through
   the system. Source -> transformations -> output. Name the functions.
   This is what the agent needs to understand before touching anything.

6. MONSTER FILES. If a file has 30+ functions, describe internal grouping.
   Which functions belong together? What are the implicit "sections"?
   This is invisible to the graph but critical for the agent.

7. WRONG APPROACHES. For each rule in rules_audit.add, describe what a
   reasonable developer would try that would break things. Not obvious
   mistakes — subtle ones that look correct.

8. RULES AUDIT. You will receive existing rules and detected patterns.
   - add: new rules based on detector evidence or your own analysis.
     3-6 high-quality rules beats 15 mediocre ones.
   - update: rules whose content or wrong_approach is outdated.
     Provide full new text for each changed field.
   - remove: rules where the pattern is gone from the codebase.
     Never remove rules with source=manual or source=migrated-from-semantic.
   - Do NOT re-propose rules listed under "User dismissed".
   Omit update/remove if empty.

9. COHERENCE RULES. Identify values, counts, names, or constants defined
   in one file but repeated as literals in other files (templates, configs,
   docs, README, tests). For each, propose a rule with category="coherence":
   - fix_approach="derived" if the value can be computed from the source
     (e.g. len(dict) instead of hardcoded count).
   - fix_approach="refactor" if it requires architectural change
     (e.g. extract shared constant).
   - fix_approach="sync" if manual sync is the only option
     (e.g. prose in README matching code behavior).
   Include sync_with listing the files that must stay in sync.

### QUALITY TEST

Before writing, check each item:
- Would the agent break something without knowing this? -> Keep
- Would the agent figure this out from reading the code? -> Remove
- Is this a general programming fact? -> Remove
- Does "affects" name a specific file? -> Keep. Says "root"? -> Fix or remove

Output: JSON matching the schema below.
Respond with valid JSON only, no markdown fences."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ZoneIntent(BaseModel):
    why: str
    wrong_approach: str


class MonsterFileSection(BaseModel):
    prefix: str
    purpose: str
    count: int = 0


class MonsterFile(BaseModel):
    sections: list[MonsterFileSection] = []
    where_to_add: str = ""


class SemanticLayer(BaseModel):
    data_flow: str = ""
    # fn_ids that the data_flow narrative is grounded in. Populated
    # deterministically from graph centrality before the LLM is called, so
    # an agent consuming `data_flow` has verified scope inputs without
    # parsing prose.
    data_flow_targets: list[str] = []
    domain_context: str = ""
    zone_intents: dict[str, ZoneIntent] = {}
    monster_files: dict[str, MonsterFile] = {}
    new_feature_checklist: list[str] = []
    constraints: list[str] = []  # user-defined external constraints, never overwritten by init
    meta: dict[str, Any] = {}


@dataclass
class EnrichResult:
    """Result of SemanticEnricher.enrich() — layer saved to disk, audit for rules."""
    layer: SemanticLayer
    rules_audit: RulesAudit = field(default_factory=RulesAudit)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class SemanticStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store_dir = root / STORE_DIR
        self.semantic_path = self.store_dir / SEMANTIC_FILE

    def save(self, data: SemanticLayer) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.semantic_path.write_text(
            data.model_dump_json(indent=2), encoding="utf-8"
        )

    def load(self) -> SemanticLayer | None:
        if not self.semantic_path.exists():
            return None
        try:
            raw = json.loads(self.semantic_path.read_text(encoding="utf-8"))
            return SemanticLayer.model_validate(raw)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Graph summary for prompt
# ---------------------------------------------------------------------------

def _graph_hash(graph: Graph, root: Path) -> str:
    """Hash of all function bodies — changes when any code changes."""
    h = hashlib.sha256()
    for fn in sorted(graph.functions.values(), key=lambda f: f.id):
        try:
            lines = (root / fn.file).read_text(encoding="utf-8").splitlines()
            body = "\n".join(lines[fn.line_start - 1:fn.line_end])
            h.update(body.encode("utf-8"))
        except Exception:
            pass
    return h.hexdigest()


def _select_data_flow_targets(
    graph: Graph, max_total: int = 12,
) -> list:
    """Pick functions to seed the `data_flow` narrative.

    Strategy (deterministic, no LLM):
    1. All route handlers — true entry points, always include first.
    2. Top-N remaining by (callers + callees), excluding dunders/private,
       requiring at least one edge so we don't list isolated functions.

    Capped at `max_total`. Returned in selection order: routes first, then
    centrality-sorted descending. Caller centrality is computed once via
    a degree map to avoid O(F*E) scans on large graphs.
    """
    # Precompute degrees once: avoids quadratic graph.callers/callees calls.
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for edge in graph.call_edges:
        in_deg[edge.target_fn] = in_deg.get(edge.target_fn, 0) + 1
        out_deg[edge.source_fn] = out_deg.get(edge.source_fn, 0) + 1

    selected: list = []
    seen: set[str] = set()

    # Routes first — entry points take precedence regardless of degree.
    for fn in graph.functions.values():
        if fn.route and fn.id not in seen:
            selected.append(fn)
            seen.add(fn.id)
            if len(selected) >= max_total:
                return selected

    # Then central functions, skipping privates and isolated ones.
    candidates = [
        fn for fn in graph.functions.values()
        if fn.id not in seen and not fn.name.startswith("_")
        and (in_deg.get(fn.id, 0) + out_deg.get(fn.id, 0)) > 0
    ]
    candidates.sort(
        key=lambda f: in_deg.get(f.id, 0) + out_deg.get(f.id, 0),
        reverse=True,
    )
    for fn in candidates:
        if len(selected) >= max_total:
            break
        selected.append(fn)
        seen.add(fn.id)
    return selected


def _format_data_flow_targets_section(targets: list, graph: Graph) -> str:
    """Render the ground-truth function list for the prompt.

    Each line gives the LLM the *display name* it should use in narrative
    (`name()` or `Class.method()`) plus structural metadata. The agent
    consuming `data_flow` later uses `data_flow_targets` (saved fn_ids)
    rather than parsing the narrative, so display fidelity matters more
    than structural fidelity in the rendered prose.
    """
    if not targets:
        return ""
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for edge in graph.call_edges:
        in_deg[edge.target_fn] = in_deg.get(edge.target_fn, 0) + 1
        out_deg[edge.source_fn] = out_deg.get(edge.source_fn, 0) + 1

    lines = [
        "## Data flow targets — REQUIRED references for `data_flow`",
        "",
        "When you write the `data_flow` field, you MUST trace data through",
        "AT LEAST 6 of the functions listed below. Reference each as",
        "`name()` or `Class.method()` exactly as displayed. Do NOT invent",
        "function names. Do NOT write a layer-only architectural overview",
        "(e.g. 'API → services → repos') — the consumer is an AI agent",
        "that needs concrete jump targets, not abstract category names.",
        "",
    ]
    for fn in targets:
        display = f"{fn.class_name}.{fn.name}" if fn.class_name else fn.name
        meta_parts = []
        if fn.route:
            meta_parts.append(f"route={fn.http_method or 'GET'} {fn.route}")
        meta_parts.append(f"callers={in_deg.get(fn.id, 0)}")
        meta_parts.append(f"callees={out_deg.get(fn.id, 0)}")
        lines.append(f"- `{display}()`  ({fn.file}; {', '.join(meta_parts)})")
    return "\n".join(lines)


def _build_project_summary(graph: Graph, root: Path) -> str:
    """Build a compact text summary of the project for the API prompt."""
    zones: dict[str, dict[str, list[str]]] = {}

    for fn in graph.functions.values():
        z = graph.file_zone(fn.file)
        zones.setdefault(z, {}).setdefault(fn.file, []).append(fn.name)

    parts = []
    for zone, files in sorted(zones.items()):
        parts.append(f"\n## Zone: {zone}")
        for file_path, fn_names in sorted(files.items()):
            # Read source file
            try:
                source = (root / file_path).read_text(encoding="utf-8")
            except Exception:
                source = ""
            parts.append(f"\n### {file_path}\n```\n{source}\n```")

    # Add import edges summary
    import_summary = []
    for edge in graph.import_edges:
        src_z = graph.file_zone(edge.source_file)
        tgt_z = graph.file_zone(edge.target_file)
        if src_z != tgt_z:
            import_summary.append(f"  {src_z} -> {tgt_z}")

    if import_summary:
        parts.append("\n## Cross-zone imports\n" + "\n".join(sorted(set(import_summary))))

    # Flag monster files (30+ functions) so model describes their sections
    monster_files = []
    for file_path, file_node in graph.files.items():
        if len(file_node.function_ids) >= 30:
            monster_files.append(
                f"  {file_path}: {len(file_node.function_ids)} functions"
            )
    if monster_files:
        parts.append(
            "\n## Monster files (30+ functions, describe sections)\n"
            + "\n".join(monster_files)
        )

    return "\n".join(parts)


SCHEMA_TEXT = """\
{
  "data_flow": "One paragraph tracing data through the system. MUST reference at least 6 functions from the 'Data flow targets' section above, formatted as `name()` or `Class.method()`. Layer-only descriptions (API → services → repos) without concrete function names are unacceptable.",
  "domain_context": "2-3 sentences: what domain concepts mean for the code.",
  "zone_intents": {
    "<zone_or_file>": {"why": "...", "wrong_approach": "..."}
  },
  "monster_files": {
    "<filename.py>": {
      "sections": [
        {"prefix": "api_carbon_*", "purpose": "carbon endpoints", "count": 5}
      ],
      "where_to_add": "new endpoints go after ..."
    }
  },
  "rules_audit": {
    "add": [
      {
        "category": "architecture|data|numeric|api|validation|errors|testing|security",
        "title": "Short rule name",
        "content": "What to do — specific, actionable, names files/functions",
        "wrong_approach": "Subtle mistake a developer would make that looks correct",
        "affects": ["specific_file.py"],
        "related": ["other_category"]
      }
    ],
    "update": [
      {"id": 1, "title": "...", "content": "...", "wrong_approach": "...", "reason": "why updated"}
    ],
    "remove": [
      {"id": 2, "reason": "why removed — pattern no longer in codebase"}
    ]
  },
  "new_feature_checklist": ["1. ...", "2. ..."]
}"""


# ---------------------------------------------------------------------------
# Context formatters for existing rules / evidence / dismissed
# ---------------------------------------------------------------------------

def _format_existing_rules(rules: list) -> str:
    if not rules:
        return ""
    lines = ["## Existing rules (audit — update if outdated, remove if irrelevant, keep if valid)"]
    for r in rules:
        lines.append(f"[{r.id}] {r.category} | {r.title}  (source: {r.source})")
        lines.append(f"    {r.content}")
        if r.wrong_approach:
            lines.append(f"    wrong_approach: {r.wrong_approach}")
    return "\n".join(lines)


def _format_evidence(evidence: list[ProposedRule]) -> str:
    if not evidence:
        return ""
    lines = ["## Detected patterns (use as evidence for rules_audit.add)"]
    for e in evidence:
        line = f"- [{e.category}] {e.title}: {e.content}"
        if e.affects:
            line += f"  (affects: {', '.join(e.affects[:3])})"
        lines.append(line)
    return "\n".join(lines)


def _format_dismissed(dismissed: DismissedFile) -> str:
    if not dismissed.dismissed_adds:
        return ""
    lines = ["## User dismissed — do NOT re-propose"]
    for d in dismissed.dismissed_adds:
        lines.append(f"- [{d.category}] {d.title}")
    return "\n".join(lines)


def _parse_rules_audit(raw: dict) -> RulesAudit:
    add = [
        RuleAdd(
            category=r.get("category", "architecture"),
            title=r.get("title", ""),
            content=r.get("content", ""),
            wrong_approach=r.get("wrong_approach", ""),
            affects=r.get("affects", []),
            related=r.get("related", []),
        )
        for r in raw.get("add", [])
        if r.get("title") and r.get("content")
    ]
    update = [
        RuleUpdate(
            id=r["id"],
            title=r.get("title", ""),
            content=r.get("content", ""),
            wrong_approach=r.get("wrong_approach", ""),
            reason=r.get("reason", ""),
        )
        for r in raw.get("update", [])
        if isinstance(r.get("id"), int)
    ]
    remove = [
        RuleRemove(id=r["id"], reason=r.get("reason", ""))
        for r in raw.get("remove", [])
        if isinstance(r.get("id"), int)
    ]
    return RulesAudit(add=add, update=update, remove=remove)


# ---------------------------------------------------------------------------
# Enricher
# ---------------------------------------------------------------------------

def _build_http_client():
    """Build httpx client. SSL verify off by default (corporate proxy compat)."""
    if os.environ.get("WINKERS_SSL_VERIFY", "0").lower() in ("1", "true", "yes"):
        return None  # use default httpx with SSL verification
    import httpx
    return httpx.Client(verify=False)


class SemanticEnricher:
    def __init__(self, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "Semantic enrichment requires the 'anthropic' package. "
                "Install with: pip install anthropic"
            )
        http_client = _build_http_client()
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if http_client:
            kwargs["http_client"] = http_client
        self._client = anthropic.Anthropic(**kwargs)
        self._model = os.environ.get("WINKERS_MODEL", DEFAULT_MODEL)

    def enrich(
        self, graph: Graph, root: Path,
        insights_text: str = "",
        existing_rules: list | None = None,
        detector_evidence: list[ProposedRule] | None = None,
        dismissed: DismissedFile | None = None,
    ) -> EnrichResult:
        """One API call -- send project code + rules context, get semantic layer + audit back."""
        project_text = _build_project_summary(graph, root)

        user_msg = "Here is the project:\n" + project_text

        # Compute data_flow targets deterministically and feed them to the LLM
        # as a required reference list. The same list is saved on the layer so
        # consumers don't need to parse the narrative for jump targets.
        data_flow_targets = _select_data_flow_targets(graph)
        targets_section = _format_data_flow_targets_section(
            data_flow_targets, graph,
        )
        if targets_section:
            user_msg += "\n\n---\n\n" + targets_section

        if insights_text:
            user_msg += "\n\n---\n\n" + insights_text

        context_sections = []
        if existing_rules:
            context_sections.append(_format_existing_rules(existing_rules))
        if detector_evidence:
            context_sections.append(_format_evidence(detector_evidence))
        if dismissed:
            dismissed_text = _format_dismissed(dismissed)
            if dismissed_text:
                context_sections.append(dismissed_text)
        if context_sections:
            user_msg += "\n\n---\n\n" + "\n\n".join(context_sections)

        user_msg += "\n\n---\n\nJSON schema:\n" + SCHEMA_TEXT

        _start = time.monotonic()
        models = [self._model]
        if self._model != FALLBACK_MODEL:
            models.append(FALLBACK_MODEL)

        last_error = None
        used_model = self._model
        response = None

        for model in models:
            for attempt in range(3):
                try:
                    response = self._client.messages.create(
                        model=model,
                        max_tokens=4096,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": user_msg}],
                    )
                    used_model = model
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    if "529" in err_str or "overloaded" in err_str.lower():
                        wait = (attempt + 1) * 5
                        time.sleep(wait)
                        continue
                    raise RuntimeError(
                        f"Semantic enrichment failed: {e}"
                    ) from e
            if response is not None:
                break

        if response is None:
            raise RuntimeError(
                f"Semantic enrichment failed after retries: {last_error}"
            )

        try:
            text = response.content[0].text
            if text.strip().startswith("```"):
                text = text.strip().split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
            # Extract rules_audit before validating SemanticLayer
            raw_audit = parsed.pop("rules_audit", {})
            layer = SemanticLayer.model_validate(parsed)
            rules_audit = _parse_rules_audit(raw_audit)
        except Exception as e:
            raise RuntimeError(f"Semantic enrichment failed: {e}") from e

        # Persist the targets the LLM was grounded on. These are graph-verified
        # fn_ids — agents can pass them directly to `scope` without re-parsing
        # the narrative or guessing.
        layer.data_flow_targets = [fn.id for fn in data_flow_targets]

        usage = getattr(response, "usage", None)
        elapsed = time.monotonic() - _start
        layer.meta = {
            "schema_version": "2",
            "model": used_model,
            "graph_hash": _graph_hash(graph, root),
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "duration_s": round(elapsed, 1),
        }
        return EnrichResult(layer=layer, rules_audit=rules_audit)

    def is_stale(self, graph: Graph, root: Path, existing: SemanticLayer) -> bool:
        """Check if any code changed since last enrichment."""
        old_hash = existing.meta.get("graph_hash", "")
        return _graph_hash(graph, root) != old_hash


def build_insights_prompt(root: Path) -> str:
    """Build prompt section from accumulated insights, if any exist."""
    from winkers.insights_store import InsightsStore

    store = InsightsStore(root)
    items = store.open_insights()
    if not items:
        return ""

    # Filter: high priority, or medium with 2+ occurrences
    relevant = [
        i for i in items
        if i.priority == "high"
        or (i.priority == "medium" and i.occurrences >= 2)
    ]
    if not relevant:
        return ""

    lines = [
        "## Known gaps from past agent sessions",
        "",
        "Previous AI agent sessions on this project revealed these",
        "knowledge gaps. Incorporate them into the semantic layer",
        "(constraints, conventions, zone_intents) so future agents",
        "have this knowledge before starting work.",
        "",
    ]

    by_target: dict[str, list] = {}
    for item in relevant:
        by_target.setdefault(item.semantic_target, []).append(item)

    for target, group in sorted(by_target.items()):
        lines.append(f"### {target}")
        for item in group:
            occ = f" (seen {item.occurrences}x)" if item.occurrences > 1 else ""
            lines.append(
                f"- [{item.category}]{occ} {item.injection_content}"
            )
        lines.append("")

    return "\n".join(lines)
