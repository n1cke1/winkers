"""Pydantic models for description-author output."""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

# Restricted vocabulary — keeps coupling aggregator's matching logic simple.
# `count` / `identifier` / `id_list` cover ~90% of real cases on CHP.
ArtifactKind = Literal[
    "count",        # numeric counter that appears elsewhere as a literal
    "identifier",   # single name/key referenced from another file
    "id_list",      # ordered/unordered set of names duplicated cross-file
    "phrase",       # human-readable phrase copied between code and UI
    "threshold",    # numeric tolerance/limit driving downstream behaviour
    "route",        # HTTP path referenced by both backend and frontend
    "other",
]


class HardcodedArtifact(BaseModel):
    """One value that, if changed in the source, requires synchronized
    changes in another place.

    `value` is canonicalized for cross-unit equality comparisons (the
    aggregator normalizes before hashing). For `id_list` kind value is a
    list of strings; for everything else, a single string. `surface`
    preserves the original wording when it differs from the canonical
    form — useful for grep-resolution when the agent needs to find the
    literal in code/UI.
    """
    value: str | list[str]
    kind: ArtifactKind
    context: str
    surface: str | None = None

    def canonical_key(self) -> str:
        """Stable string for cross-unit equality (aggregator hash key).

        Lists are sorted before serialization so insertion order doesn't
        produce false-different keys for equivalent sets.
        """
        if isinstance(self.value, list):
            return json.dumps(sorted(self.value), ensure_ascii=False)
        return str(self.value)


class Description(BaseModel):
    """Result of one description-author run on a single unit."""
    description: str
    hardcoded_artifacts: list[HardcodedArtifact] = []


def parse_description_response(raw: str) -> Description | None:
    """Parse LLM JSON output into a Description, tolerant of preamble/fences.

    Real-world LLM responses sometimes include preamble text ("Now I have
    full context.\\n\\n{...}") or markdown fences. We extract the first
    well-formed top-level JSON object and try to validate it; failures at
    either step return None so the caller can retry on next init.
    """
    obj_text = _extract_first_json_object(raw)
    if obj_text is None:
        log.debug("description: no JSON object in response (first 80c: %r)",
                  raw[:80])
        return None
    try:
        data = json.loads(obj_text)
    except json.JSONDecodeError as e:
        log.debug("description JSON parse failed: %s", e)
        return None
    try:
        return Description.model_validate(data)
    except ValidationError as e:
        log.debug("description schema validation failed: %s", e)
        return None


def _extract_first_json_object(text: str) -> str | None:
    """Return the substring of `text` covering the first balanced {...}.

    Tracks strings/escapes so braces inside JSON-string values don't
    throw off the depth counter. Returns None if no balanced object
    found.
    """
    text = text.strip()
    # Strip leading markdown fences ```json or ```
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
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
                return text[start:i + 1]
    return None
