"""Description-author subsystem.

Generates rich per-unit descriptions (~100 words) with structured
`hardcoded_artifacts` lists. Two unit kinds share one output schema:

- `function_unit` — anchored to graph fn_id; description regenerates when
  the function's AST hash changes.
- `traceability_unit` (template section) — anchored to file+id; description
  regenerates when the section content hash changes.

Hardcoded artifacts feed the coupling aggregator, which finds cross-file
links by intersecting artifact values across units. This replaces the
previous project-wide LLM coherence-rules pass with distributed, scalable
per-unit extraction.
"""

from winkers.descriptions.models import Description, HardcodedArtifact
from winkers.descriptions.prompts import (
    format_data_file_prompt,
    format_function_prompt,
    format_template_section_prompt,
)

__all__ = [
    "Description",
    "HardcodedArtifact",
    "format_data_file_prompt",
    "format_function_prompt",
    "format_template_section_prompt",
]
