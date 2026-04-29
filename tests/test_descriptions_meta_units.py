"""Wave 4c-2 — prompts for class / attribute / value units.

Author functions themselves spawn `claude --print` and require the
binary on PATH; we test only the deterministic pieces (prompt
rendering, response parsing) here. The integration with
_run_units_pipeline gets exercised by `winkers init --with-units`
on a real fixture project — out of scope for unit tests.
"""

from __future__ import annotations

from winkers.descriptions.prompts import (
    format_attribute_prompt,
    format_class_prompt,
    format_value_prompt,
)

# ---------------------------------------------------------------------------
# class_unit prompt
# ---------------------------------------------------------------------------


class TestClassPrompt:
    def test_includes_class_header(self):
        prompt = format_class_prompt(
            class_name="Client",
            file_path="app/repos/client.py",
            line_start=12,
            line_end=89,
            base_classes=["Base", "TimestampMixin"],
            method_signatures=["def get_invoices(self)", "def soft_delete(self)"],
            attribute_lines=[
                "invoices = relationship('Invoice', back_populates='client')",
            ],
        )
        assert "CLASS: Client" in prompt
        assert "app/repos/client.py:12-89" in prompt
        assert "BASES: Base, TimestampMixin" in prompt
        assert "def get_invoices(self)" in prompt
        assert "relationship('Invoice'" in prompt

    def test_no_bases_no_methods_still_renders(self):
        prompt = format_class_prompt(
            class_name="Empty",
            file_path="x.py",
            line_start=1, line_end=2,
            base_classes=[],
            method_signatures=[],
            attribute_lines=[],
        )
        assert "CLASS: Empty" in prompt
        assert "BASES" not in prompt
        assert "METHODS" not in prompt
        assert "CLASS-BODY ATTRIBUTES" not in prompt

    def test_docstring_included_when_present(self):
        prompt = format_class_prompt(
            class_name="Client",
            file_path="x.py",
            line_start=1, line_end=10,
            base_classes=[],
            method_signatures=[],
            attribute_lines=[],
            docstring="Customer record with billing references.",
        )
        assert "DOCSTRING:" in prompt
        assert "Customer record" in prompt

    def test_output_schema_referenced(self):
        """Sanity — every prompt asks for the JSON schema."""
        prompt = format_class_prompt(
            "X", "x.py", 1, 1, [], [], [],
        )
        assert "Output JSON ONLY" in prompt
        assert "hardcoded_artifacts" in prompt


# ---------------------------------------------------------------------------
# attribute_unit prompt
# ---------------------------------------------------------------------------


class TestAttributePrompt:
    def test_includes_constructor_and_annotation(self):
        prompt = format_attribute_prompt(
            name="Client.invoices",
            class_name="Client",
            file_path="app/repos/client.py",
            line=17,
            ctor="relationship",
            annotation="Mapped[List[Invoice]]",
            source_line=(
                "    invoices: Mapped[List[Invoice]] = "
                "relationship(back_populates='client')"
            ),
        )
        assert "ATTRIBUTE: Client.invoices" in prompt
        assert "app/repos/client.py:17" in prompt
        assert "CONSTRUCTOR: relationship" in prompt
        assert "ANNOTATION: Mapped[List[Invoice]]" in prompt
        assert "back_populates='client'" in prompt

    def test_owning_class_summary_optional(self):
        prompt = format_attribute_prompt(
            name="Client.invoices",
            class_name="Client",
            file_path="x.py", line=1,
            ctor="relationship", annotation="",
            source_line="    invoices = relationship('Invoice')",
            class_summary="",
        )
        assert "OWNING CLASS" not in prompt

        prompt2 = format_attribute_prompt(
            name="Client.invoices",
            class_name="Client",
            file_path="x.py", line=1,
            ctor="relationship", annotation="",
            source_line="    invoices = relationship('Invoice')",
            class_summary="Customer record with billing references.",
        )
        assert "OWNING CLASS (Client)" in prompt2
        assert "Customer record" in prompt2


# ---------------------------------------------------------------------------
# value_unit prompt
# ---------------------------------------------------------------------------


class TestValuePrompt:
    def test_lists_values_inline(self):
        prompt = format_value_prompt(
            name="VALID_STATUSES",
            file_path="status.py",
            line=3,
            kind="set",
            values=["draft", "sent", "paid"],
            consumer_count=4,
            consumer_files=["status.py", "service.py"],
        )
        assert "COLLECTION: VALID_STATUSES" in prompt
        assert "KIND: set" in prompt
        # repr-quoted values appear in the prompt
        assert "'draft'" in prompt
        assert "'sent'" in prompt
        assert "'paid'" in prompt
        assert "CONSUMERS: 4 function(s) across 2 file(s)" in prompt

    def test_truncates_long_values_with_remainder_marker(self):
        values = [f"v{i}" for i in range(40)]
        prompt = format_value_prompt(
            name="MANY", file_path="x.py", line=1, kind="set",
            values=values, consumer_count=0, consumer_files=[],
        )
        # Cap is 32 — 8 should be elided.
        assert "(+8 more elided)" in prompt

    def test_empty_consumers_block_omitted(self):
        prompt = format_value_prompt(
            name="LONELY", file_path="x.py", line=1, kind="set",
            values=["a", "b", "c"],
            consumer_count=0, consumer_files=[],
        )
        assert "CONSUMER FILES:" not in prompt
