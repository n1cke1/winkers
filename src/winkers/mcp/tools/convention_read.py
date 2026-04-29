"""MCP tool: convention_read — zone intent / data_flow / domain_context / checklist."""

from __future__ import annotations

from pathlib import Path

from winkers.mcp.tools import _load_semantic


def _tool_convention_read(args: dict, root: Path) -> dict:
    target = args.get("target", "")
    semantic = _load_semantic(root)

    if semantic is None:
        return {"error": "No semantic.json found. Run winkers init."}

    # Aspect names
    if target == "data_flow":
        return {
            "data_flow": semantic.data_flow or "Not available.",
            "data_flow_targets": semantic.data_flow_targets,
        }
    if target == "domain_context":
        return {"domain_context": semantic.domain_context or "Not available."}
    if target == "checklist":
        return {"checklist": semantic.new_feature_checklist}
    if target == "constraints":
        return {"constraints": semantic.constraints}

    # Zone name
    if target in semantic.zone_intents:
        intent = semantic.zone_intents[target]
        return {
            "zone": target,
            "why": intent.why,
            "wrong_approach": intent.wrong_approach,
        }

    # File path (monster file)
    if target in semantic.monster_files:
        mf = semantic.monster_files[target]
        return {
            "file": target,
            "sections": [s.model_dump() for s in mf.sections],
            "where_to_add": mf.where_to_add,
        }

    return {
        "error": f"Target '{target}' not found.",
        "available_zones": list(semantic.zone_intents.keys()),
        "available_files": list(semantic.monster_files.keys()),
        "aspects": ["data_flow", "domain_context", "checklist", "constraints"],
    }
