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

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from winkers.audit.prompts import empty_pending_marker, format_audit_prompt
from winkers.audit.runner import run_audit
from winkers.audit.selector import build_packet, compute_changed_files
from winkers.descriptions.store import UnitsStore
from winkers.hooks._logger import log_hook
from winkers.hooks.session_start import (
    clear_baseline,
    read_baseline,
)

log = logging.getLogger(__name__)

PENDING_FILENAME = ".winkers_pending.md"


def _read_session_id() -> str:
    """Best-effort session_id read.

    The spawning wrapper hook detaches us with stdin=DEVNULL, so stdin
    is usually empty here. The wrapper passes session_id via the
    `WINKERS_SESSION_ID` env var when available; we also try stdin in
    case this entry is invoked manually (tests / direct CLI use).
    """
    env_id = os.environ.get("WINKERS_SESSION_ID", "")
    if env_id:
        return env_id
    try:
        payload = sys.stdin.read()
    except Exception:
        return ""
    if not payload:
        return ""
    try:
        return str(json.loads(payload).get("session_id", ""))
    except Exception:
        return ""


def run(root: Path) -> None:
    """SessionEnd entry — see module docstring for full pipeline."""
    session_id = _read_session_id()

    with log_hook(root, session_id, "SessionEnd", "stop_audit") as rec:
        pending_path = root / PENDING_FILENAME

        base_commit = read_baseline(root)
        changed_files = compute_changed_files(root, base_commit)
        rec["changed_files"] = len(changed_files)

        if not changed_files:
            _write_pending(pending_path, empty_pending_marker(), reason="no changes")
            clear_baseline(root)
            rec["outcome"] = "no_changes"
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
            rec["outcome"] = "no_units"
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
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        rec["changed_units"] = len(packet.changed_units)
        rec["related_couplings"] = len(packet.related_couplings)

        if packet.is_empty:
            _write_pending(pending_path, empty_pending_marker(), reason="empty packet")
            clear_baseline(root)
            rec["outcome"] = "empty_packet"
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
            rec["outcome"] = "audit_empty"
            return

        _write_pending(pending_path, output, reason="audit succeeded")
        clear_baseline(root)
        rec["outcome"] = "audit_succeeded"


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
