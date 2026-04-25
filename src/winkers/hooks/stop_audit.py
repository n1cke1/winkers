"""SessionEnd hook: cross-file coherence audit.

Orchestrates:
  1. Read SessionStart baseline commit.
  2. Compute changed files (committed + uncommitted).
  3. Build audit packet from existing units.json + couplings.
  4. Spawn `claude --print` (read-only) with the audit prompt.
  5. Write the LLM's TODO checklist to `.winkers_pending.md`.
  6. Clear the SessionStart baseline file.

If the packet is empty (no changes, or no relevant couplings),
writes the empty marker so the next session knows there's nothing
to inject and prompt-enrich can short-circuit.

This entry point is intentionally synchronous — the spawning hook
(see `cli.main.hook_stop_audit_spawn`) is responsible for
detaching it so SessionEnd doesn't block on the ~30s LLM call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from winkers.audit.prompts import empty_pending_marker, format_audit_prompt
from winkers.audit.runner import run_audit
from winkers.audit.selector import build_packet, compute_changed_files
from winkers.descriptions.store import UnitsStore
from winkers.hooks.session_start import (
    clear_baseline,
    read_baseline,
)

log = logging.getLogger(__name__)

PENDING_FILENAME = ".winkers_pending.md"


def run(root: Path) -> None:
    """SessionEnd entry — see module docstring for full pipeline."""
    pending_path = root / PENDING_FILENAME

    base_commit = read_baseline(root)
    changed_files = compute_changed_files(root, base_commit)

    if not changed_files:
        _write_pending(pending_path, empty_pending_marker(), reason="no changes")
        clear_baseline(root)
        return

    units = UnitsStore(root).load()
    if not units:
        log.info("audit: no units.json — skipping. Run `winkers init --with-units`.")
        _write_pending(
            pending_path,
            empty_pending_marker(),
            reason="no units index",
        )
        clear_baseline(root)
        return

    head_commit = ""
    try:
        import subprocess
        head_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root), text=True, encoding="utf-8",
            errors="replace", stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        pass

    packet = build_packet(
        changed_files=changed_files,
        units=units,
        meta={
            "base_commit": base_commit or "",
            "head_commit": head_commit,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    if packet.is_empty:
        _write_pending(pending_path, empty_pending_marker(), reason="empty packet")
        clear_baseline(root)
        return

    prompt = format_audit_prompt(packet)
    log.info(
        "audit: %d changed files, %d changed units, %d related couplings",
        len(packet.changed_files),
        len(packet.changed_units),
        len(packet.related_couplings),
    )
    output = run_audit(prompt)

    if not output:
        log.warning("audit: empty output, writing marker")
        _write_pending(pending_path, empty_pending_marker(), reason="audit failed")
        clear_baseline(root)
        return

    _write_pending(pending_path, output, reason="audit succeeded")
    clear_baseline(root)


def _write_pending(path: Path, content: str, reason: str = "") -> None:
    """Write the pending markdown atomically.

    The next interactive session's prompt-enrich hook will pick this
    up and inject (or short-circuit on the empty marker).
    """
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content + "\n", encoding="utf-8")
        tmp.replace(path)
        log.debug("pending written (%s) → %s", reason, path)
    except Exception as e:
        log.warning("could not write %s: %s", path, e)
