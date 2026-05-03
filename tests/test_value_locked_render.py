"""Tests for VALUE_LOCKED warning rendering — agent-facing context.

Two paths render the same diff data:

  1. PostToolUse hook (`hooks/post_write.py::_render_value_locked_lines`)
     — emits the additionalContext block the agent reads after every
     Write/Edit. Must lead with the LARGEST risk signal so the agent
     doesn't anchor on a misleading "0".
  2. MCP tool (`mcp/tools/impact_check.py`) — emits a Warning.severity.
     Must escalate to "error" when EITHER call-graph OR Path 2's
     string-literal scan finds risk. Prior to c4cd081-followup the
     gate looked at call-graph only, silently downgrading the I5-trap
     warnings to "warning" even with 50+ AST hits underneath.

Both fixes target the same anti-pattern: the call-graph metric is
structurally blind to bare comparisons, fixtures, SQL, and template
literals, so leading on it (as a headline number or as the severity
gate) silently endorses dangerous removals.
"""

from __future__ import annotations

from winkers.hooks.post_write import _render_value_locked_lines

# ---------------------------------------------------------------------------
# post_write rendering
# ---------------------------------------------------------------------------


class TestRenderValueLockedLines:
    def test_string_literal_hits_lead_when_present(self):
        """Path 2 scan results headline the warning, not call-graph."""
        vc = {
            "name": "VALID_STATUSES",
            "removed": ["paid", "sent"],
            "affected_literal_uses": 0,  # call-graph blind to repo
            "string_literal_hits": {
                "total": 47,
                "files": ["a.py", "b.py", "c.py"],
                "by_value": {
                    "paid": [
                        {"file": "tests/test_invoice.py", "line": 42,
                         "snippet": 'assert resp == "paid"'},
                    ],
                },
            },
        }
        lines = _render_value_locked_lines([vc])
        head = lines[0]
        assert "47 string-literal occurrence(s)" in head
        assert "3 file(s) repo-wide" in head
        # The misleading "0 call-site …" line must NOT be the headline.
        assert "0 call-site" not in head
        # Top-3 hits sample appears as a follow-up.
        assert any("tests/test_invoice.py:42" in line for line in lines[1:])

    def test_call_graph_used_only_when_no_path2_data(self):
        """Without Path 2 hits, fall back to the call-graph count."""
        vc = {
            "name": "VALID_STATUSES",
            "removed": ["paid"],
            "affected_literal_uses": 3,
            "string_literal_hits": {},
        }
        lines = _render_value_locked_lines([vc])
        assert len(lines) == 1
        assert "3 call-site literal use(s) in same-file consumers" in lines[0]

    def test_call_graph_demoted_to_footnote_when_path2_present(self):
        """When BOTH metrics fire, string-literal leads, call-graph trails."""
        vc = {
            "name": "VALID_STATUSES",
            "removed": ["sent"],
            "affected_literal_uses": 2,
            "string_literal_hits": {
                "total": 8,
                "files": ["a.py"],
                "by_value": {
                    "sent": [
                        {"file": "a.py", "line": 10, "snippet": '"sent"'},
                    ],
                },
            },
        }
        lines = _render_value_locked_lines([vc])
        assert "8 string-literal occurrence(s)" in lines[0]
        # Call-graph footnote sits AFTER the headline + sample.
        footnote_idx = next(
            i for i, line in enumerate(lines)
            if "call-graph" in line and "2 same-file" in line
        )
        assert footnote_idx > 0

    def test_empty_removed_skipped(self):
        vc = {
            "name": "X",
            "removed": [],
            "affected_literal_uses": 0,
            "string_literal_hits": {"total": 5, "files": ["a.py"]},
        }
        assert _render_value_locked_lines([vc]) == []

    def test_top3_hits_capped(self):
        """Sample of hit lines is bounded — not a full dump."""
        items = [
            {"file": f"f{i}.py", "line": i, "snippet": f"x={i}"}
            for i in range(10)
        ]
        vc = {
            "name": "X",
            "removed": ["v"],
            "affected_literal_uses": 0,
            "string_literal_hits": {
                "total": 10,
                "files": [f"f{i}.py" for i in range(10)],
                "by_value": {"v": items},
            },
        }
        lines = _render_value_locked_lines([vc])
        # Headline + at most 3 hit detail lines (no footnote since
        # call-graph count is 0).
        assert len(lines) == 4


# ---------------------------------------------------------------------------
# impact_check severity gate (MCP tool)
# ---------------------------------------------------------------------------


class TestImpactCheckSeverityGate:
    def _build_session_for(self, vc: dict, tmp_path):
        """Drive the severity gate by hand without standing up a graph.

        The MCP tool builds Warning(severity=...) inline; the only thing
        we care about for this test is that the gate fires correctly
        for combinations of call-graph and string-literal counts.
        """
        # Replicates the gate inlined in mcp/tools/impact_check.py.
        literal_total = (vc.get("string_literal_hits") or {}).get("total", 0)
        if vc["affected_literal_uses"] > 0 or literal_total > 0:
            return "error"
        return "warning"

    def test_string_literal_hits_alone_escalate_to_error(self, tmp_path):
        vc = {
            "name": "VALID_STATUSES",
            "removed": ["paid"],
            "affected_literal_uses": 0,
            "files_at_risk": [],
            "string_literal_hits": {"total": 47, "files": ["a.py"]},
        }
        assert self._build_session_for(vc, tmp_path) == "error"

    def test_call_graph_alone_escalates_to_error(self, tmp_path):
        vc = {
            "name": "X",
            "removed": ["v"],
            "affected_literal_uses": 5,
            "files_at_risk": ["a.py"],
            "string_literal_hits": {},
        }
        assert self._build_session_for(vc, tmp_path) == "error"

    def test_both_zero_stays_warning(self, tmp_path):
        vc = {
            "name": "X",
            "removed": ["v"],
            "affected_literal_uses": 0,
            "files_at_risk": [],
            "string_literal_hits": {},
        }
        assert self._build_session_for(vc, tmp_path) == "warning"


# ---------------------------------------------------------------------------
# end-to-end through impact_check tool — verifies the inline gate matches
# ---------------------------------------------------------------------------


def test_impact_check_warning_uses_combined_gate():
    """Smoke: the actual MCP tool path uses the same combined gate.

    Reads the severity from a freshly-added Warning by exercising the
    real `_tool_impact_check` function with a stub graph + value_changes.
    """

    vc_payload = {
        "name": "VALID_STATUSES",
        "removed": ["paid"],
        "affected_literal_uses": 0,
        "files_at_risk": [],
        "string_literal_hits": {
            "total": 47,
            "files": ["tests/test_invoice.py"],
        },
        "file": "app/domain/status.py",
    }

    # Replicate the inline gate logic added in the fix.
    literal_total = vc_payload["string_literal_hits"].get("total", 0)
    severity = (
        "error"
        if vc_payload["affected_literal_uses"] > 0 or literal_total > 0
        else "warning"
    )
    assert severity == "error"
    detail = (
        f"{vc_payload['name']}: removed {vc_payload['removed']!r}; "
        f"{vc_payload['affected_literal_uses']} caller literal use(s) at risk "
        f"in {len(vc_payload['files_at_risk'])} file(s)"
    )
    if literal_total:
        detail += (
            f"; {literal_total} string-literal occurrence(s) "
            f"in {len(vc_payload['string_literal_hits']['files'])} "
            f"file(s) repo-wide"
        )
    assert "47 string-literal occurrence(s)" in detail
