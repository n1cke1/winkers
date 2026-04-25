"""UserPromptSubmit hook — detect creation intent and inject before_create results.

Phase 3 extension: also reads `.winkers_pending.md` (audit TODO list
left by the previous SessionEnd) and prepends it to the agent's
context. Pending file is archived after consumption so each TODO
list is shown exactly once.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# Patterns that indicate creation intent
_CREATION_PATTERNS = re.compile(
    r"\b(add|create|implement|write|build|make|introduce|new)\b"
    r".*\b(function|method|class|module|endpoint|route|handler|component|service)\b",
    re.IGNORECASE,
)


def has_creation_intent(prompt: str) -> bool:
    """Check if user prompt indicates intent to create new code."""
    return bool(_CREATION_PATTERNS.search(prompt))


def extract_intent(prompt: str) -> str:
    """Extract the creation intent from the user prompt for before_create search."""
    # Remove common noise words and return the core intent
    # The full prompt is usually good enough for search
    noise = r"\b(please|can you|could you|i need to|i want to)\b"
    cleaned = re.sub(noise, "", prompt, flags=re.IGNORECASE)
    return cleaned.strip()[:200]


PENDING_FILENAME = ".winkers_pending.md"
EMPTY_PENDING_MARKER = "- (no coherence drift detected)"


def _consume_pending(root: Path) -> str | None:
    """Read pending.md, archive it, and return injection text (or None).

    Always archives after read so each pending list shows once. The
    empty marker yields None (nothing to inject) — but the file is
    still archived.
    """
    pending_path = root / PENDING_FILENAME
    if not pending_path.exists():
        return None
    try:
        content = pending_path.read_text(encoding="utf-8").strip()
    except Exception:
        return None

    # Move to history before deciding to inject — so even an unparseable
    # pending file gets cleared instead of repeating every prompt.
    try:
        history_dir = root / ".winkers" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        # Microsecond suffix so two SessionStarts within the same second
        # don't overwrite each other (rare in prod, common in tests).
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        archive = history_dir / f"pending_{ts}.md"
        archive.write_text(content, encoding="utf-8")
        pending_path.unlink(missing_ok=True)
    except Exception:
        pass

    if not content or content == EMPTY_PENDING_MARKER:
        return None
    return content


def run(root: Path) -> None:
    """Read hook JSON from stdin, build additionalContext, output enrichment.

    Composes two sources of additional context (in order):
      1. Audit TODO from previous SessionEnd (`.winkers_pending.md`).
      2. before_create matches when the prompt looks like creation intent.
    """
    try:
        hook_data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # Non-blocking: let prompt pass through.

    user_prompt = hook_data.get("user_prompt", "") or ""

    sections: list[str] = []

    # ── 1. Audit TODO from prior session ─────────────────────────────────
    pending_text = _consume_pending(root)
    if pending_text:
        sections.append(
            "[Winkers] Cross-file coherence TODO from previous session "
            "audit — verify or knock these off before unrelated work:\n"
            + pending_text
        )

    # ── 2. before_create on creation intent (existing behaviour) ────────
    if user_prompt and has_creation_intent(user_prompt):
        from winkers.search import (
            format_before_create_response,
            search_functions,
        )
        from winkers.store import GraphStore

        store = GraphStore(root)
        graph = store.load()
        if graph is not None:
            intent = extract_intent(user_prompt)
            matches = search_functions(graph, intent)
            if matches:
                result = format_before_create_response(
                    graph, intent, matches, root=root,
                )
                lines = [
                    "[Winkers] Existing implementations found before "
                    "creating new code:"
                ]
                existing = result.get("existing", [])
                for item in existing[:3]:
                    fn_name = item.get("function", "")
                    file_path = item.get("file", "")
                    score = item.get("score", 0)
                    sig = item.get("signature", "")
                    lines.append(
                        f"  - {fn_name}{sig} in {file_path} "
                        f"(score: {score:.2f})"
                    )
                suggestion = result.get("suggestion")
                if suggestion:
                    lines.append(f"  SUGGESTION: {suggestion}")
                lines.append(
                    "  Consider reusing existing code before "
                    "writing new functions."
                )
                sections.append("\n".join(lines))

    if not sections:
        sys.exit(0)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(sections),
        }
    }
    print(json.dumps(output))
    sys.exit(0)
