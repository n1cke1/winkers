"""project.json — unified project-level semantic + rules artifact.

Wave 4a of the redesign (CONCEPT.md §3 "File model"). Replaces the two
separate files `.winkers/semantic.json` and `.winkers/rules/rules.json`
with a single `.winkers/project.json` containing both as top-level
sections:

```
{
  "version": 1,
  "semantic": { data_flow, domain_context, zones, ... },
  "rules":    { project, config, rules: [...] }
}
```

Rationale
---------
Both artifacts are project-level, both LLM-generated at init, both
consumed by `orient` (`conventions` / `rules_list` includes). Keeping
two files multiplies the "reload from disk" surface area for orient
and makes section-level migrations awkward.

Migration
---------
`ProjectStore.load_or_default()` performs a one-shot migration: if
`project.json` doesn't exist but legacy `semantic.json` and/or
`rules/rules.json` are present, their contents are pulled in and a
fresh `project.json` is written. The legacy files are kept on disk
read-only — users who roll back to a prior winkers don't lose state.

`SemanticStore` / `RulesStore` continue to expose their existing
APIs and delegate to `ProjectStore` under the hood. This keeps the
~80 call sites across the codebase working without churn.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from winkers.conventions import RulesFile
from winkers.semantic import SemanticLayer
from winkers.store import STORE_DIR

log = logging.getLogger(__name__)

PROJECT_FILE = "project.json"
PROJECT_VERSION = 1

# Legacy paths — read once for migration, never written.
_LEGACY_SEMANTIC = "semantic.json"
_LEGACY_RULES_DIR = "rules"
_LEGACY_RULES_FILE = "rules.json"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ProjectFile(BaseModel):
    """Container for both semantic and rules sections."""

    version: int = PROJECT_VERSION
    semantic: SemanticLayer = Field(default_factory=SemanticLayer)
    rules: RulesFile = Field(default_factory=RulesFile)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ProjectStore:
    """Persistence + migration for `.winkers/project.json`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.store_dir = self.root / STORE_DIR
        self.path = self.store_dir / PROJECT_FILE

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def load(self) -> ProjectFile | None:
        """Return parsed project.json, or None if missing/invalid.

        Does NOT auto-migrate from legacy — callers that want migration
        should use `load_or_default()`. This split keeps `load()` cheap
        for hot paths that just want to ask "does it exist".
        """
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ProjectFile.model_validate(data)
        except Exception as e:
            log.warning("project.json malformed (%s); treating as empty", e)
            return None

    def save(self, project: ProjectFile) -> None:
        """Atomically persist the full project bundle.

        Bumps `version` to PROJECT_VERSION on every save so older readers
        can detect a newer file format.
        """
        project.version = PROJECT_VERSION
        self.store_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            project.model_dump_json(indent=2), encoding="utf-8",
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    # Migration + safe defaults
    # ------------------------------------------------------------------

    def load_or_default(self) -> ProjectFile:
        """Return the project bundle, migrating legacy files if needed.

        Order of resolution:
          1. If project.json exists and parses → return it.
          2. Else look for legacy `semantic.json` + `rules/rules.json`
             — if either exists, build a ProjectFile from them and
             write project.json so subsequent loads are direct.
          3. Else return an empty default ProjectFile (NOT written —
             callers that want a stub on disk should `save()` it
             explicitly).
        """
        existing = self.load()
        if existing is not None:
            return existing

        migrated = self._migrate_from_legacy()
        if migrated is not None:
            self.save(migrated)
            log.info(
                "project.json: migrated from legacy "
                "semantic.json + rules/rules.json"
            )
            return migrated

        return ProjectFile()

    def _migrate_from_legacy(self) -> ProjectFile | None:
        """Read legacy files (one-shot). Returns None if nothing on disk.

        Either file can be missing — we use whatever is available and
        leave the other section at its default. This means a project
        that only ever ran rules (no semantic enrichment) still gets
        a clean migration.
        """
        semantic = _load_legacy_semantic(self.store_dir)
        rules = _load_legacy_rules(self.store_dir)
        if semantic is None and rules is None:
            return None
        bundle = ProjectFile()
        if semantic is not None:
            bundle.semantic = semantic
        if rules is not None:
            bundle.rules = rules
        return bundle

    # ------------------------------------------------------------------
    # Section-level conveniences (used by SemanticStore / RulesStore shims)
    # ------------------------------------------------------------------

    def update_semantic(self, semantic: SemanticLayer) -> None:
        """Mutate the semantic section and persist."""
        bundle = self.load_or_default()
        bundle.semantic = semantic
        self.save(bundle)

    def update_rules(self, rules: RulesFile) -> None:
        """Mutate the rules section and persist."""
        bundle = self.load_or_default()
        bundle.rules = rules
        self.save(bundle)


# ---------------------------------------------------------------------------
# Legacy loaders — strictly internal, used only by migration
# ---------------------------------------------------------------------------

def _load_legacy_semantic(store_dir: Path) -> SemanticLayer | None:
    path = store_dir / _LEGACY_SEMANTIC
    if not path.exists():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return SemanticLayer.model_validate(raw)
    except Exception as e:
        log.warning(
            "legacy %s present but unparseable (%s); skipping migration",
            _LEGACY_SEMANTIC, e,
        )
        return None


def _load_legacy_rules(store_dir: Path) -> RulesFile | None:
    path = store_dir / _LEGACY_RULES_DIR / _LEGACY_RULES_FILE
    if not path.exists():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return RulesFile.model_validate(raw)
    except Exception as e:
        log.warning(
            "legacy %s/%s present but unparseable (%s); skipping migration",
            _LEGACY_RULES_DIR, _LEGACY_RULES_FILE, e,
        )
        return None
