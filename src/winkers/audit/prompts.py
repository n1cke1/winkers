"""Audit prompt template + formatter.

Single self-contained prompt — the auditor LLM receives the entire
context as one user message. No tool descriptions to memorize, no
iterative back-and-forth: read the change, check coupled artifacts,
output a markdown checklist.

The prompt is in English (universal); we tell the LLM to match the
project's language for prose. Domain terms in artifact contexts are
preserved as-is.
"""

from __future__ import annotations

import json
from textwrap import dedent

from winkers.audit.selector import AuditPacket


_AUDIT_INSTRUCTIONS = dedent("""
    You are auditing code changes for CROSS-FILE COHERENCE DRIFT.

    A change in one file may require synchronized updates elsewhere
    when shared values, identifier lists, or domain phrases are
    duplicated across the codebase. Your job: decide whether each
    coupled artifact needs an update, and emit a TODO checklist.

    PROCESS

    1. Read the change summary.
    2. For each coupled artifact, check whether the change touched
       its source side. If yes, downstream consumers may now be
       stale — flag them.
    3. Don't flag couplings whose source side wasn't touched.
    4. Don't flag couplings where contexts in different consumers
       are clearly unrelated domains (false-positive matches).

    AVAILABLE TOOLS: Read, Grep, Glob (read-only). Use them sparingly
    — only to verify items you're unsure about. Do not edit files.

    OUTPUT

    Markdown checklist, one item per line:
      - [ ] file:line — action — rationale

    Use the file paths from the consumers below. Keep `action` concrete
    (rename X to Y, update counter to N, sync field name). Keep
    `rationale` to one short clause.

    If nothing requires sync, output exactly:
      - (no coherence drift detected)

    Output ONLY the checklist. No preamble, no postscript.
""").strip()


def format_audit_prompt(packet: AuditPacket) -> str:
    """Render the full prompt for one audit call.

    All sections are bounded — `packet` is already truncated by the
    selector — so the prompt size stays under ~8k tokens even on
    large refactors.
    """
    parts = [_AUDIT_INSTRUCTIONS, ""]

    # Changes summary
    parts.append("CHANGES")
    parts.append("---")
    if packet.meta.get("base_commit"):
        parts.append(
            f"Diff: {packet.meta['base_commit'][:8]}.."
            f"{packet.meta.get('head_commit', 'HEAD')[:8]}"
        )
    parts.append(f"Changed files ({len(packet.changed_files)}):")
    for f in packet.changed_files:
        parts.append(f"  - {f}")
    parts.append("")

    # Changed units (function_unit / template_unit anchored to changed files)
    parts.append(f"CHANGED UNITS ({len(packet.changed_units)})")
    parts.append("---")
    if packet.changed_units:
        for u in packet.changed_units:
            uid = u.get("id", "<unknown>")
            kind = u.get("kind", "")
            desc = (u.get("description") or "")[:200]
            parts.append(f"  [{kind}] {uid}")
            if desc:
                parts.append(f"    {desc}...")
            artifacts = u.get("hardcoded_artifacts") or []
            if artifacts:
                parts.append(f"    artifacts: {len(artifacts)}")
                for a in artifacts[:5]:
                    val = a.get("value")
                    if isinstance(val, list):
                        val = "[" + ", ".join(str(v) for v in val) + "]"
                    parts.append(
                        f"      • {a.get('kind','?')}={str(val)[:30]}  "
                        f"({a.get('context','')[:60]})"
                    )
    else:
        parts.append("  (no units in index match the changed files)")
    parts.append("")

    # Related couplings
    parts.append(f"COUPLED ARTIFACTS THAT MAY BE STALE ({len(packet.related_couplings)})")
    parts.append("---")
    if packet.related_couplings:
        for c in packet.related_couplings:
            meta = c.get("meta") or {}
            value = meta.get("canonical_value", "")
            kind = meta.get("primary_kind", "")
            parts.append(
                f"  • value={value!r} ({kind}, "
                f"{meta.get('file_count', 0)} files, "
                f"{meta.get('hit_count', 0)} hits)"
            )
            for cn in c.get("consumers", []):
                wtc = (cn.get("what_to_check") or "")[:120]
                parts.append(
                    f"      ↳ {cn.get('file', '')} :: "
                    f"{cn.get('anchor', '')[:50]}"
                )
                if wtc:
                    parts.append(f"        {wtc}")
    else:
        parts.append("  (no coupling units touch the changed files)")
    parts.append("")

    return "\n".join(parts)


def empty_pending_marker() -> str:
    """Stable single-line marker the prompt-enrich hook recognizes as
    'no drift to inject'. Keeps the inject path simple."""
    return "- (no coherence drift detected)"
