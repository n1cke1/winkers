"""Convention rules — structured project conventions with MCP observability."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from winkers.store import STORE_DIR

RULES_DIR = "rules"
RULES_FILE = "rules.json"
OVERVIEW_FILE = "overview.md"
TRACES_DIR = "traces"

RuleSource = Literal["semantic-agent", "auto-detected", "manual", "migrated-from-semantic"]


# ---------------------------------------------------------------------------
# ProposedRule — transient detector evidence, never serialised to disk
# ---------------------------------------------------------------------------

@dataclass
class ProposedRule:
    """Evidence from detectors — fed to SemanticEnricher as context."""
    category: str
    title: str
    content: str
    wrong_approach: str = ""
    affects: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit types — returned by SemanticEnricher, applied after user review
# ---------------------------------------------------------------------------

@dataclass
class RuleAdd:
    """A new rule proposed by the enricher."""
    category: str
    title: str
    content: str
    wrong_approach: str = ""
    affects: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


@dataclass
class RuleUpdate:
    """An existing rule to be updated (full replacement of changed fields)."""
    id: int
    title: str = ""
    content: str = ""
    wrong_approach: str = ""
    reason: str = ""


@dataclass
class RuleRemove:
    """An existing rule to be removed."""
    id: int
    reason: str = ""


@dataclass
class RulesAudit:
    """Holistic audit result from SemanticEnricher."""
    add: list[RuleAdd] = field(default_factory=list)
    update: list[RuleUpdate] = field(default_factory=list)
    remove: list[RuleRemove] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.add and not self.update and not self.remove


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RuleStats(BaseModel):
    times_requested: int = 0
    times_applied: int = 0
    times_confident_wrong: int = 0


class ConventionRule(BaseModel):
    id: int
    category: str  # architecture | data | numeric | api | validation | errors | testing | security
    title: str
    content: str
    wrong_approach: str = ""
    related: list[str] = []  # other category names
    affects: list[str] = []  # specific files or zones, used by scope()
    source: RuleSource
    created: str  # ISO date YYYY-MM-DD
    stats: RuleStats = RuleStats()


class RulesConfig(BaseModel):
    mode: str = "hybrid"
    overview_max_tokens: int = 300
    auto_analyze: bool = True
    suggest_model: str = "claude-haiku-4-5-20251001"


class RulesFile(BaseModel):
    version: int = 1
    project: str = ""
    config: RulesConfig = RulesConfig()
    rules: list[ConventionRule] = []


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class RulesStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rules_dir = root / STORE_DIR / RULES_DIR
        self.rules_path = self.rules_dir / RULES_FILE
        self.overview_path = self.rules_dir / OVERVIEW_FILE
        self.traces_dir = self.rules_dir / TRACES_DIR

    def load(self) -> RulesFile:
        if not self.rules_path.exists():
            return RulesFile()
        try:
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            return RulesFile.model_validate(data)
        except Exception:
            return RulesFile()

    def save(self, rules_file: RulesFile) -> None:
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        self.rules_path.write_text(
            rules_file.model_dump_json(indent=2), encoding="utf-8"
        )

    def next_id(self, rules_file: RulesFile) -> int:
        """Return next available integer ID (never reuses deleted IDs)."""
        if not rules_file.rules:
            return 1
        return max(r.id for r in rules_file.rules) + 1

    def add_rule(self, rule: ConventionRule) -> None:
        rules_file = self.load()
        rules_file.rules.append(rule)
        self.save(rules_file)

    def delete_rule(self, rule_id: int) -> bool:
        """Delete rule by ID. Returns True if found and deleted."""
        rules_file = self.load()
        before = len(rules_file.rules)
        rules_file.rules = [r for r in rules_file.rules if r.id != rule_id]
        if len(rules_file.rules) < before:
            self.save(rules_file)
            return True
        return False

    def exists(self) -> bool:
        return self.rules_path.exists()


# ---------------------------------------------------------------------------
# Overview compiler
# ---------------------------------------------------------------------------

def compile_overview(rules_file: RulesFile, path: Path) -> None:
    """Generate overview.md from rules, respecting overview_max_tokens budget.

    Priority: manual > semantic-agent/auto-detected by topic alpha order.
    Rough token estimate: len(text) / 4.
    """
    max_tokens = rules_file.config.overview_max_tokens
    header = "# Project conventions (use orient/rule_read tools for details)\n"
    lines: list[str] = []

    by_category: dict[str, ConventionRule] = {}
    for rule in rules_file.rules:
        # Keep one rule per category — prefer manual, then first encountered
        existing = by_category.get(rule.category)
        if existing is None or rule.source == "manual":
            by_category[rule.category] = rule

    for category in sorted(by_category):
        rule = by_category[category]
        # One-line summary: first sentence of content
        summary = rule.content.split(".")[0].strip()
        lines.append(f"- {category}: {summary}")

    # Trim to budget
    content = header
    for line in lines:
        candidate = content + line + "\n"
        if len(candidate) / 4 > max_tokens:
            break
        content = candidate

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Trace logger
# ---------------------------------------------------------------------------

class TraceLogger:
    def __init__(self, root: Path, session_id: str) -> None:
        traces_dir = root / STORE_DIR / RULES_DIR / TRACES_DIR
        traces_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._path = traces_dir / f"{today}_{session_id}.jsonl"

    def log(self, event: dict[str, Any]) -> None:
        from datetime import datetime
        entry = {"timestamp": datetime.now(UTC).isoformat(), **event}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Dismissed — rules the user rejected, persisted to avoid re-proposing
# ---------------------------------------------------------------------------

DISMISSED_FILE = "dismissed.json"


class DismissedAdd(BaseModel):
    category: str
    title: str
    dismissed_at: str  # ISO date YYYY-MM-DD


class DismissedFile(BaseModel):
    dismissed_adds: list[DismissedAdd] = []
    dismissed_removes: list[int] = []   # rule IDs user vetoed removal of
    dismissed_updates: list[int] = []   # rule IDs user vetoed update of


class DismissedStore:
    def __init__(self, root: Path) -> None:
        self._path = root / STORE_DIR / RULES_DIR / DISMISSED_FILE

    def load(self) -> DismissedFile:
        if not self._path.exists():
            return DismissedFile()
        try:
            return DismissedFile.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except Exception:
            return DismissedFile()

    def save(self, dismissed: DismissedFile) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(dismissed.model_dump_json(indent=2), encoding="utf-8")

    def merge(
        self,
        dismissed_adds: list[RuleAdd],
        dismissed_remove_ids: list[int],
        dismissed_update_ids: list[int],
    ) -> None:
        """Merge new dismissals into the existing dismissed file."""
        from datetime import date
        current = self.load()
        existing_keys = {(d.category, d.title) for d in current.dismissed_adds}
        today = date.today().isoformat()
        for item in dismissed_adds:
            if (item.category, item.title) not in existing_keys:
                current.dismissed_adds.append(
                    DismissedAdd(category=item.category, title=item.title, dismissed_at=today)
                )
        current.dismissed_removes = list(
            set(current.dismissed_removes) | set(dismissed_remove_ids)
        )
        current.dismissed_updates = list(
            set(current.dismissed_updates) | set(dismissed_update_ids)
        )
        self.save(current)
