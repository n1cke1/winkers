"""Semantic layer — architectural context that cannot be computed from code structure."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from winkers.models import Graph
from winkers.store import STORE_DIR

SEMANTIC_FILE = "semantic.json"
DEFAULT_MODEL = "claude-sonnet-4-20250514"

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

7. WRONG APPROACHES. For each convention, describe what a reasonable
   developer would try that would break things. Not obvious mistakes —
   subtle ones that look correct.

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


class Constraint(BaseModel):
    id: str
    name: str
    why: str
    severity: str  # critical | important | convention
    affects: list[str] = []


class Convention(BaseModel):
    rule: str
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
    domain_context: str = ""
    zone_intents: dict[str, ZoneIntent] = {}
    monster_files: dict[str, MonsterFile] = {}
    constraints: list[Constraint] = []
    conventions: list[Convention] = []
    new_feature_checklist: list[str] = []
    meta: dict[str, Any] = {}


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

def _infer_zone(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else "root"


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


def _build_project_summary(graph: Graph, root: Path) -> str:
    """Build a compact text summary of the project for the API prompt."""
    zones: dict[str, dict[str, list[str]]] = {}

    for fn in graph.functions.values():
        z = _infer_zone(fn.file)
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
        src_z = _infer_zone(edge.source_file)
        tgt_z = _infer_zone(edge.target_file)
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
  "data_flow": "One paragraph: how data moves through the system, naming key functions.",
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
  "constraints": [
    {"id": "C001", "name": "...", "why": "...",
     "severity": "critical|important", "affects": ["specific_file.py"]}
  ],
  "conventions": [
    {"rule": "...", "wrong_approach": "subtle mistake that looks correct"}
  ],
  "new_feature_checklist": ["1. ...", "2. ..."]
}"""


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
    ) -> SemanticLayer:
        """One API call -- send project code, get semantic layer back."""
        project_text = _build_project_summary(graph, root)

        user_msg = (
            "Here is the project:\n"
            + project_text
        )

        if insights_text:
            user_msg += "\n\n---\n\n" + insights_text

        user_msg += "\n\n---\n\nJSON schema:\n" + SCHEMA_TEXT

        _start = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text
            if text.strip().startswith("```"):
                text = text.strip().split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
            layer = SemanticLayer.model_validate(parsed)
        except Exception as e:
            raise RuntimeError(f"Semantic enrichment failed: {e}") from e

        usage = getattr(response, "usage", None)
        elapsed = time.monotonic() - _start
        layer.meta = {
            "model": self._model,
            "graph_hash": _graph_hash(graph, root),
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "duration_s": round(elapsed, 1),
        }
        return layer

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
