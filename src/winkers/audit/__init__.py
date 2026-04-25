"""Phase 3 — coherence audit subsystem.

After a Claude session ends, an out-of-band audit subprocess inspects
which files changed and finds related coupling units. The audit LLM
(via `claude --print`, read-only tools) decides whether the change
requires synchronized updates elsewhere and writes a TODO checklist
to `.winkers_pending.md`.

The next interactive session reads pending.md via the
prompt-enrich hook and injects it into the user's first prompt, so
the agent starts knowing what to verify before writing more code.

This closes the loop: Phase 1 builds the index, Phase 2 searches it,
Phase 3 audits coherence between sessions.
"""

from winkers.audit.prompts import format_audit_prompt
from winkers.audit.runner import run_audit
from winkers.audit.selector import AuditPacket, build_packet, compute_changed_files

__all__ = [
    "AuditPacket",
    "build_packet",
    "compute_changed_files",
    "format_audit_prompt",
    "run_audit",
]
