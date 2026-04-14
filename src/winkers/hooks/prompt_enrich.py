"""UserPromptSubmit hook — detect creation intent and inject before_create results."""

from __future__ import annotations

import json
import re
import sys
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


def run(root: Path) -> None:
    """Read hook JSON from stdin, detect creation intent, output enrichment."""
    try:
        hook_data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # Non-blocking: let it pass

    user_prompt = hook_data.get("user_prompt", "")
    if not user_prompt or not has_creation_intent(user_prompt):
        # No creation intent — allow silently
        sys.exit(0)

    # Load graph and run before_create search
    from winkers.search import format_before_create_response, search_functions
    from winkers.store import GraphStore

    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        sys.exit(0)

    intent = extract_intent(user_prompt)
    matches = search_functions(graph, intent)

    if not matches:
        sys.exit(0)

    result = format_before_create_response(graph, intent, matches, root=root)

    # Format as concise additional context
    lines = ["[Winkers] Existing implementations found before creating new code:"]
    existing = result.get("existing", [])
    for item in existing[:3]:
        fn_name = item.get("function", "")
        file_path = item.get("file", "")
        score = item.get("score", 0)
        sig = item.get("signature", "")
        lines.append(f"  - {fn_name}{sig} in {file_path} (score: {score:.2f})")

    suggestion = result.get("suggestion")
    if suggestion:
        lines.append(f"  SUGGESTION: {suggestion}")

    lines.append("  Consider reusing existing code before writing new functions.")

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(lines),
        }
    }
    print(json.dumps(output))
    sys.exit(0)
