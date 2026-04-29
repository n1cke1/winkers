"""Tests for the class + class-attribute scanner (Wave 5a)."""

from __future__ import annotations

from pathlib import Path

from winkers.class_attrs import (
    attribute_unit_id,
    build_attribute_units,
    build_class_units,
    class_unit_id,
    detect_class_attrs,
)
from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.target_resolution import resolve_targets


def _build_graph(root: Path):
    g = GraphBuilder().build(root)
    CrossFileResolver().resolve(g, str(root))
    detect_class_attrs(g, root)
    return g


# ---------------------------------------------------------------------------
# Detector — class definitions
# ---------------------------------------------------------------------------


class TestClassDetection:
    def test_simple_class(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        names = [c.name for c in g.class_definitions]
        assert "Client" in names
        cls = next(c for c in g.class_definitions if c.name == "Client")
        assert cls.file == "models.py"
        assert cls.line_start == 1

    def test_base_classes_extracted(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Base:\n    pass\n\n"
            "class Mixin:\n    pass\n\n"
            "class Client(Base, Mixin):\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        cls = next(c for c in g.class_definitions if c.name == "Client")
        assert "Base" in cls.base_classes
        assert "Mixin" in cls.base_classes


# ---------------------------------------------------------------------------
# Detector — class attributes
# ---------------------------------------------------------------------------


class TestAttributeDetection:
    def test_relationship_style(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    invoices = relationship('Invoice', back_populates='client')\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        attrs = [a.name for a in g.class_attributes]
        assert "Client.invoices" in attrs
        attr = next(a for a in g.class_attributes if a.name == "Client.invoices")
        assert attr.ctor == "relationship"
        assert attr.class_name == "Client"
        assert attr.attr_name == "invoices"
        assert attr.line >= 1

    def test_pydantic_field_style(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Settings:\n"
            "    debug = Field(default=False, description='enable debug')\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        attr = next(a for a in g.class_attributes if a.name == "Settings.debug")
        assert attr.ctor == "Field"

    def test_dotted_constructor(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    invoices = orm.relationship('Invoice')\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        attr = next(a for a in g.class_attributes if a.name == "Client.invoices")
        assert attr.ctor == "relationship"  # attribute name, not the receiver

    def test_skips_literal_only_assignment(self, tmp_path: Path):
        """``MAX_RETRIES = 5`` is NOT a class attribute candidate (no call)."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    MAX_RETRIES = 5\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        names = [a.name for a in g.class_attributes]
        assert "Client.MAX_RETRIES" not in names


# ---------------------------------------------------------------------------
# Unit builders
# ---------------------------------------------------------------------------


class TestUnitBuilders:
    def test_class_unit_basics(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client(Base):\n"
            "    invoices = relationship('Invoice')\n"
            "    payments = relationship('Payment')\n\n"
            "    def __init__(self): pass\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        units = build_class_units(g, proj)
        u = next(u for u in units if u["name"] == "Client")
        assert u["id"] == class_unit_id("models.py", "Client")
        assert u["kind"] == "class_unit"
        assert u["base_classes"] == ["Base"]
        assert u["method_count"] == 2
        assert u["attribute_count"] == 2
        assert "lines 1-" in u["summary"]
        assert "Base" in u["summary"]

    def test_attribute_unit_basics(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    invoices = relationship('Invoice', back_populates='client')\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        units = build_attribute_units(g, proj)
        u = next(u for u in units if u["name"] == "Client.invoices")
        assert u["id"] == attribute_unit_id("models.py", "Client", "invoices")
        assert u["kind"] == "attribute_unit"
        assert u["class_name"] == "Client"
        assert u["attr_name"] == "invoices"
        assert u["ctor"] == "relationship"
        assert "relationship" in u["summary"]
        assert u["description"] == ""  # filled later by Wave 4c LLM pass

    def test_unit_ids_stable(self):
        assert class_unit_id("a/b.py", "Foo") == "class:a/b.py::Foo"
        assert attribute_unit_id("a/b.py", "Foo", "bar") == (
            "attr:a/b.py::Foo.bar"
        )


# ---------------------------------------------------------------------------
# Resolver — Class.attr resolution against graph.class_attributes
# ---------------------------------------------------------------------------


class TestResolverClassAttr:
    def test_resolves_class_attr(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    invoices = relationship('Invoice')\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        targets = resolve_targets(
            "fix Client.invoices relationship cascade", g,
        )
        assert "Client.invoices" in targets.attributes
        assert "models.py" in targets.paths

    def test_resolves_multiple_attrs(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    invoices = relationship('Invoice')\n"
            "    payments = relationship('Payment')\n"
            "    contracts = relationship('Contract')\n"
            "    def hello(self): return 'hi'\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        targets = resolve_targets(
            "fix Client.invoices, Client.payments, Client.contracts", g,
        )
        assert set(targets.attributes) == {
            "Client.invoices", "Client.payments", "Client.contracts",
        }

    def test_does_not_attribute_when_method_exists(self, tmp_path: Path):
        """`Class.method()` (parens form) routes to function, not attribute."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "models.py").write_text(
            "class Client:\n"
            "    def invoices(self): return []\n",
            encoding="utf-8",
        )
        g = _build_graph(proj)
        # The method form (with parens) — should resolve as a function,
        # NOT as an attribute.
        targets = resolve_targets("fix Client.invoices()", g)
        assert any(
            "Client.invoices" in fid or "::invoices" in fid
            for fid in targets.functions
        )
        # No spurious attribute hit:
        assert targets.attributes == []
