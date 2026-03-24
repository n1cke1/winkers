"""Tests for rules.py violation detection."""


from winkers.models import (
    CallEdge,
    CallSite,
    FunctionNode,
    Graph,
    ImportEdge,
    Param,
)
from winkers.rules import check_violations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fn(fn_id: str, file: str = "a.py", params=None, return_type=None, is_exported=False):
    return FunctionNode(
        id=fn_id,
        file=file,
        name=fn_id.split("::")[-1],
        kind="function",
        language="python",
        line_start=1,
        line_end=10,
        params=params or [],
        return_type=return_type,
        is_exported=is_exported,
    )


def _edge(src: str, tgt: str, src_file: str = "a.py", line: int = 5):
    return CallEdge(
        source_fn=src,
        target_fn=tgt,
        call_site=CallSite(
            caller_fn_id=src,
            file=src_file,
            line=line,
            expression=f"{tgt}()",
        ),
        confidence=1.0,
    )


def _import_edge(src_file: str, tgt_file: str):
    return ImportEdge(source_file=src_file, target_file=tgt_file, names=[])


def _graph(*fns, call_edges=None, import_edges=None):
    g = Graph()
    for fn in fns:
        g.functions[fn.id] = fn
    g.call_edges = call_edges or []
    g.import_edges = import_edges or []
    return g


# ---------------------------------------------------------------------------
# Rule 1: signature_changed / function_removed
# ---------------------------------------------------------------------------

class TestSignatureChanged:
    def test_no_change_no_violation(self):
        fn = _fn("a.py::foo", params=[Param(name="x", type_hint="int")])
        old = _graph(fn, call_edges=[_edge("a.py::bar", "a.py::foo")])
        new = _graph(
            _fn("a.py::foo", params=[Param(name="x", type_hint="int")]),
            call_edges=[_edge("a.py::bar", "a.py::foo")],
        )
        violations = check_violations(old, new)
        assert violations == []

    def test_signature_changed_locked_raises_error(self):
        old_fn = _fn("a.py::foo", params=[Param(name="x", type_hint="int")])
        new_fn = _fn(
            "a.py::foo",
            params=[Param(name="x", type_hint="int"), Param(name="y", type_hint="str")],
        )
        old = _graph(old_fn, call_edges=[_edge("a.py::bar", "a.py::foo")])
        new = _graph(new_fn, call_edges=[_edge("a.py::bar", "a.py::foo")])

        violations = check_violations(old, new)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == "signature_changed"
        assert v.severity == "error"
        assert v.function == "a.py::foo"
        assert "a.py::bar" in v.affected

    def test_signature_changed_unlocked_no_violation(self):
        """Unlocked function (no callers) can change signature freely."""
        old_fn = _fn("a.py::foo", params=[Param(name="x", type_hint="int")])
        new_fn = _fn("a.py::foo", params=[Param(name="y", type_hint="str")])
        old = _graph(old_fn)
        new = _graph(new_fn)

        violations = check_violations(old, new)
        sig_violations = [v for v in violations if v.rule == "signature_changed"]
        assert sig_violations == []

    def test_function_removed_locked_raises_error(self):
        fn = _fn("a.py::foo")
        old = _graph(fn, call_edges=[_edge("a.py::bar", "a.py::foo")])
        new = _graph()  # foo removed

        violations = check_violations(old, new)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == "function_removed"
        assert v.severity == "error"
        assert v.function == "a.py::foo"

    def test_function_removed_unlocked_no_violation(self):
        fn = _fn("a.py::foo")
        old = _graph(fn)  # no callers → unlocked
        new = _graph()

        violations = check_violations(old, new)
        assert [v for v in violations if v.rule == "function_removed"] == []

    def test_return_type_change_locked(self):
        old_fn = _fn("a.py::foo", return_type="int")
        new_fn = _fn("a.py::foo", return_type="str")
        old = _graph(old_fn, call_edges=[_edge("a.py::bar", "a.py::foo")])
        new = _graph(new_fn, call_edges=[_edge("a.py::bar", "a.py::foo")])

        violations = check_violations(old, new)
        assert any(v.rule == "signature_changed" for v in violations)


# ---------------------------------------------------------------------------
# Rule 2: cross_zone_import
# ---------------------------------------------------------------------------

class TestCrossZoneImport:
    def _zones_config(self, rules):
        return {"zones": rules}

    def test_no_zones_config_no_violation(self):
        g = _graph(import_edges=[_import_edge("api/a.py", "db/b.py")])
        violations = check_violations(g, g)
        assert [v for v in violations if v.rule == "cross_zone_import"] == []

    def test_allowed_cross_zone_no_violation(self):
        g = _graph(import_edges=[_import_edge("api/a.py", "db/b.py")])
        config = self._zones_config({"api": {"can_import": ["db"]}})
        violations = check_violations(g, g, config)
        assert [v for v in violations if v.rule == "cross_zone_import"] == []

    def test_forbidden_cross_zone_raises_warning(self):
        g = _graph(import_edges=[_import_edge("api/a.py", "db/b.py")])
        config = self._zones_config({"api": {"can_import": []}})
        violations = check_violations(g, g, config)
        czv = [v for v in violations if v.rule == "cross_zone_import"]
        assert len(czv) == 1
        assert czv[0].severity == "warning"
        assert czv[0].file == "api/a.py"

    def test_same_zone_no_violation(self):
        g = _graph(import_edges=[_import_edge("api/a.py", "api/b.py")])
        config = self._zones_config({"api": {"can_import": []}})
        violations = check_violations(g, g, config)
        assert [v for v in violations if v.rule == "cross_zone_import"] == []

    def test_zones_as_list(self):
        """zones config can be a plain list instead of dict."""
        g = _graph(import_edges=[_import_edge("api/a.py", "db/b.py")])
        config = {"zones": {"api": ["db"]}}
        violations = check_violations(g, g, config)
        assert [v for v in violations if v.rule == "cross_zone_import"] == []


# ---------------------------------------------------------------------------
# Rule 3: circular_dependency
# ---------------------------------------------------------------------------

class TestCircularDependency:
    def test_no_cycle_no_violation(self):
        g = _graph(import_edges=[
            _import_edge("a.py", "b.py"),
            _import_edge("b.py", "c.py"),
        ])
        violations = check_violations(g, g)
        assert [v for v in violations if v.rule == "circular_dependency"] == []

    def test_simple_cycle(self):
        g = _graph(import_edges=[
            _import_edge("a.py", "b.py"),
            _import_edge("b.py", "a.py"),
        ])
        violations = check_violations(g, g)
        cyc = [v for v in violations if v.rule == "circular_dependency"]
        assert len(cyc) == 1
        assert cyc[0].severity == "warning"
        assert "a.py" in cyc[0].affected or "b.py" in cyc[0].affected

    def test_three_way_cycle(self):
        g = _graph(import_edges=[
            _import_edge("a.py", "b.py"),
            _import_edge("b.py", "c.py"),
            _import_edge("c.py", "a.py"),
        ])
        violations = check_violations(g, g)
        cyc = [v for v in violations if v.rule == "circular_dependency"]
        assert len(cyc) == 1

    def test_self_import_cycle(self):
        g = _graph(import_edges=[_import_edge("a.py", "a.py")])
        violations = check_violations(g, g)
        cyc = [v for v in violations if v.rule == "circular_dependency"]
        assert len(cyc) == 1


# ---------------------------------------------------------------------------
# Rule 4: orphan_export
# ---------------------------------------------------------------------------

class TestOrphanExport:
    def test_exported_with_callers_no_violation(self):
        fn = _fn("a.py::foo", is_exported=True)
        g = _graph(fn, call_edges=[_edge("b.py::bar", "a.py::foo")])
        violations = check_violations(g, g)
        assert [v for v in violations if v.rule == "orphan_export"] == []

    def test_exported_no_callers_info(self):
        fn = _fn("a.py::foo", is_exported=True)
        g = _graph(fn)  # no callers → not locked
        violations = check_violations(g, g)
        ov = [v for v in violations if v.rule == "orphan_export"]
        assert len(ov) == 1
        assert ov[0].severity == "info"
        assert ov[0].function == "a.py::foo"

    def test_not_exported_no_callers_no_violation(self):
        fn = _fn("a.py::foo", is_exported=False)
        g = _graph(fn)
        violations = check_violations(g, g)
        assert [v for v in violations if v.rule == "orphan_export"] == []

    def test_exported_locked_no_orphan_violation(self):
        """Exported function that IS called should not be an orphan."""
        fn = _fn("a.py::foo", is_exported=True)
        g = _graph(fn, call_edges=[_edge("b.py::bar", "a.py::foo")])
        violations = check_violations(g, g)
        assert [v for v in violations if v.rule == "orphan_export"] == []


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

def test_violation_to_dict():
    from winkers.rules import Violation
    v = Violation(
        rule="signature_changed",
        severity="error",
        function="a.py::foo",
        file="a.py",
        detail="sig changed",
        affected=["b.py::bar"],
    )
    d = v.to_dict()
    assert d["rule"] == "signature_changed"
    assert d["severity"] == "error"
    assert d["function"] == "a.py::foo"
    assert d["affected"] == ["b.py::bar"]
