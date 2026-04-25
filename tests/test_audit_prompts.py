"""Tests for winkers.audit.prompts — prompt formatter."""

from __future__ import annotations

from winkers.audit.prompts import (
    empty_pending_marker,
    format_audit_prompt,
)
from winkers.audit.selector import AuditPacket


def _packet(**overrides) -> AuditPacket:
    defaults = dict(
        changed_files=["a.py"],
        changed_units=[],
        related_couplings=[],
        meta={},
    )
    defaults.update(overrides)
    return AuditPacket(**defaults)


def test_prompt_includes_changed_files():
    p = _packet(changed_files=["a.py", "b.py"])
    out = format_audit_prompt(p)
    assert "a.py" in out
    assert "b.py" in out


def test_prompt_handles_empty_changed_units():
    p = _packet(changed_files=["a.py"])
    out = format_audit_prompt(p)
    assert "(no units in index" in out


def test_prompt_handles_empty_couplings():
    p = _packet(changed_files=["a.py"])
    out = format_audit_prompt(p)
    assert "(no coupling units" in out


def test_prompt_includes_unit_artifacts():
    unit = {
        "id": "a.py::f1",
        "kind": "function_unit",
        "description": "x",
        "hardcoded_artifacts": [
            {"value": "33", "kind": "count",
             "context": "MILP variable count"},
        ],
    }
    out = format_audit_prompt(_packet(changed_units=[unit]))
    assert "33" in out
    assert "MILP variable count" in out


def test_prompt_includes_coupling_consumers():
    coupling = {
        "id": "coupling:identifier:abc",
        "consumers": [
            {"file": "a.py", "anchor": "f1",
             "what_to_check": "verify rename"},
            {"file": "b.py", "anchor": "f2",
             "what_to_check": "sync param"},
        ],
        "meta": {
            "canonical_value": "K_regen",
            "primary_kind": "identifier",
            "file_count": 2, "hit_count": 2,
        },
    }
    out = format_audit_prompt(_packet(related_couplings=[coupling]))
    assert "K_regen" in out
    assert "verify rename" in out
    assert "sync param" in out


def test_prompt_includes_diff_commits_when_provided():
    p = _packet(meta={
        "base_commit": "abc12345", "head_commit": "def67890",
    })
    out = format_audit_prompt(p)
    assert "abc12345" in out
    assert "def67890" in out


def test_prompt_emits_no_drift_marker_format():
    """The empty marker is the agreed-upon no-op signal between audit
    and prompt-enrich. Both modules must use the same string."""
    from winkers.hooks.prompt_enrich import EMPTY_PENDING_MARKER
    assert empty_pending_marker() == EMPTY_PENDING_MARKER


def test_prompt_truncates_long_descriptions():
    """Long descriptions are clipped to keep prompt under context limits."""
    long_desc = "x" * 5000
    unit = {
        "id": "a.py::f1",
        "kind": "function_unit",
        "description": long_desc,
        "hardcoded_artifacts": [],
    }
    out = format_audit_prompt(_packet(changed_units=[unit]))
    # The 5000-char desc should be truncated; total prompt stays bounded.
    assert len(out) < 4000
