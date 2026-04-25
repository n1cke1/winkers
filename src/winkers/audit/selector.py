"""Audit packet selector.

Pure data transformations: given a list of changed file paths and the
project's units list, return the units the auditor needs to inspect.

Two roles for units in a packet:
  - `changed_units` — function_units / template_units anchored to
    changed files. These are the source of the change.
  - `related_couplings` — coupling units whose `consumers` touch any
    changed file. These are downstream sync points that may now be
    stale.

`compute_changed_files` is the IO-bound git wrapper that produces
the input list. Kept separate so `build_packet` can be unit-tested
without git.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Maximum sizes to keep prompt within Claude context window — even a
# huge refactor produces a packet small enough to fit in 8k tokens.
_MAX_CHANGED_UNITS = 30
_MAX_COUPLINGS = 40


@dataclass
class AuditPacket:
    """Inputs for the audit prompt — what changed + what may need sync.

    `meta` carries optional context (commits, timestamp) that the
    prompt formatter can include for the auditor's situational
    awareness. It is NOT consumed by the LLM directly.
    """
    changed_files: list[str] = field(default_factory=list)
    changed_units: list[dict] = field(default_factory=list)
    related_couplings: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """Nothing to audit — selector should advise skipping the call."""
        return (
            not self.changed_files
            and not self.changed_units
            and not self.related_couplings
        )


# ---------------------------------------------------------------------------
# Pure packet construction
# ---------------------------------------------------------------------------

def build_packet(
    changed_files: list[str],
    units: list[dict],
    meta: dict | None = None,
) -> AuditPacket:
    """Build an AuditPacket from changed files + the project's units list.

    Pure function — no I/O. The caller is responsible for producing
    `changed_files` (typically via `compute_changed_files`) and loading
    `units` (typically via `UnitsStore.load`).

    Truncates `changed_units` and `related_couplings` to keep the
    eventual prompt bounded. Sort order:
      - changed_units: anchored function_units first, then template_units
      - related_couplings: highest file_count first (most cross-cutting)
    """
    cf_set = set(changed_files)
    if not cf_set:
        return AuditPacket(meta=meta or {})

    fn_changed: list[dict] = []
    tpl_changed: list[dict] = []

    for u in units:
        kind = u.get("kind")
        if kind == "function_unit":
            anchor = u.get("anchor") or {}
            if anchor.get("file") in cf_set:
                fn_changed.append(u)
        elif u.get("id", "").startswith("template:"):
            sf = u.get("source_files") or []
            if any(f in cf_set for f in sf):
                tpl_changed.append(u)

    changed_units = (fn_changed + tpl_changed)[:_MAX_CHANGED_UNITS]

    related: list[dict] = []
    for u in units:
        if not u.get("id", "").startswith("coupling:"):
            continue

        # Primary signal: consumer file path matches a changed file.
        consumer_files = {
            cn.get("file") for cn in u.get("consumers", [])
        }
        if consumer_files & cf_set:
            related.append(u)
            continue

        # Secondary signal: a what_to_check or surface mentions the
        # changed file path as a substring. Catches couplings where
        # the file is referenced in prose ("при изменении data/X.json
        # обновить ...") but not as a structural consumer.file.
        joined_text = "\n".join(
            (cn.get("what_to_check") or "") + " " + (cn.get("surface") or "")
            for cn in u.get("consumers", [])
        )
        if any(f in joined_text for f in cf_set if f):
            related.append(u)

    # Most cross-cutting first — those are the riskiest drift surfaces.
    related.sort(
        key=lambda c: (
            (c.get("meta") or {}).get("file_count", 0),
            (c.get("meta") or {}).get("hit_count", 0),
        ),
        reverse=True,
    )
    related_couplings = related[:_MAX_COUPLINGS]

    return AuditPacket(
        changed_files=changed_files,
        changed_units=changed_units,
        related_couplings=related_couplings,
        meta=meta or {},
    )


# ---------------------------------------------------------------------------
# git wrapper
# ---------------------------------------------------------------------------

def compute_changed_files(
    root: Path,
    base_commit: str | None = None,
) -> list[str]:
    """Return relative paths of files changed since `base_commit`.

    Includes both committed (between base and HEAD) and uncommitted
    (working tree + index) changes. If `base_commit` is empty or None,
    falls back to HEAD~1 — the last commit.

    Returns [] on any git error (silent so the SessionEnd hook can
    still degrade gracefully on non-git checkouts).
    """
    try:
        files: set[str] = set()

        # Committed changes between base..HEAD
        if base_commit:
            out = _git(["diff", "--name-only", f"{base_commit}..HEAD"], root)
            files.update(_split_lines(out))
        else:
            try:
                out = _git(["diff", "--name-only", "HEAD~1..HEAD"], root)
                files.update(_split_lines(out))
            except subprocess.CalledProcessError:
                pass  # repo with no prior commit — skip

        # Uncommitted: working tree vs index, and index vs HEAD
        files.update(_split_lines(_git(["diff", "--name-only"], root)))
        files.update(_split_lines(_git(["diff", "--name-only", "--cached"], root)))

        return sorted(files)
    except Exception as e:
        log.warning("compute_changed_files failed: %s", e)
        return []


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(
        ["git"] + args,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stderr=subprocess.DEVNULL,
    )


def _split_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]
