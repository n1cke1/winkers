"""Data-file unit subsystem.

Indexes structured project files (JSON, YAML) as semantic units
alongside function_units and template_units. Without this, changes
to data/*.json files produce empty audit packets in Phase 3 — the
gap discovered when the topology JSON change couldn't surface
related code couplings.

Each data file becomes one `traceability_unit` with id
`data:<rel_path>` (analogous to `template:<path>#<section>`).
The LLM's hardcoded_artifacts emission then feeds the same
coupling aggregator, producing cross-file links between data
file values and their consumers in code/templates.
"""

from winkers.data_files.scanner import (
    DataFileEntry,
    discover_data_files,
    read_data_file,
)

__all__ = [
    "DataFileEntry",
    "discover_data_files",
    "read_data_file",
]
