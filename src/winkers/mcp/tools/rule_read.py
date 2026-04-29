"""MCP tool: rule_read — return all rules of a given category."""

from __future__ import annotations

from pathlib import Path

from winkers.mcp.tools._common import _load_rules


def _tool_rule_read(args: dict, root: Path) -> dict:
    category = args.get("category", "")
    rules_file = _load_rules(root)

    matches = [r for r in rules_file.rules if r.category == category]
    if not matches:
        available = sorted({r.category for r in rules_file.rules})
        return {"error": f"No rules for category '{category}'.", "available": available}

    return {
        "category": category,
        "rules": [
            {
                "id": r.id,
                "title": r.title,
                "content": r.content,
                "wrong_approach": r.wrong_approach,
                "affects": r.affects,
                "related": r.related,
            }
            for r in matches
        ],
    }
