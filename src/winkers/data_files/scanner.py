"""Discover and read data files (JSON/YAML/TOML) for unit indexing.

Strategy: include known data directories (`data/`, `config/`) but
exclude obvious noise (caches, logs, large blobs). A user can override
include/exclude patterns via `.winkers/config.toml` `[data_files]`
section.

Size guard: files larger than `MAX_FILE_BYTES` are skipped — feeding
500KB JSON to the LLM bloats the prompt and the output is unlikely to
add proportional value. Tunable.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Default extensions we treat as data files.
_DATA_EXTENSIONS = {".json", ".yaml", ".yml", ".toml"}

# Default include patterns — files inside these directories qualify.
_DEFAULT_INCLUDE_DIRS = ("data", "config", "configs", "settings")

# Default exclude patterns — known noise files. Globs evaluated against
# relative path with forward-slash separators.
_DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "**/*cache*.json",
    "**/*calib_cache*.json",
    "**/access.log",
    "**/access*.json",
    "**/userchat*.json",
    "**/*.log",
    "**/wip/**",            # in-progress per-ticket markdown
    "**/scenarios/**",      # often hundreds of saved-result JSONs
    "**/_*.json",           # dot-prefix or underscore caches
    "**/*.lock.json",
)

# Path-component-based exclusion. A file is dropped if ANY of its
# parent directories matches one of these names. Catches
# `data/node_modules/...` regardless of nesting depth (where glob
# `node_modules/**` would only match top-level).
_IGNORE_DIR_NAMES: frozenset[str] = frozenset({
    "node_modules", "__pycache__", ".venv", "venv", ".git",
    "dist", "build", ".winkers",
})

# Files larger than this are skipped — too expensive in tokens for
# uncertain payoff. CHP's tespy_topology.json is ~30KB; this leaves
# headroom for moderate growth without indexing log dumps.
MAX_FILE_BYTES = 200_000


@dataclass
class DataFileEntry:
    """One indexed data file, ready to feed to the description-author."""
    path: Path        # absolute on disk
    rel_path: str     # forward-slash relative to project root
    content: str      # decoded text (UTF-8 best-effort)
    bytes_size: int   # encoded size — used for the cache key downstream


def discover_data_files(
    root: Path,
    include_dirs: tuple[str, ...] | None = None,
    exclude_globs: tuple[str, ...] | None = None,
) -> list[Path]:
    """Walk the project, yield candidate data file paths.

    Inclusion: file is under one of `include_dirs` AND has a recognised
    extension AND no exclude glob matches.

    `include_dirs` are matched against the FIRST path component only
    — so `data/scenarios/x.json` is under `data` (qualifies the
    directory check), but exclude_globs can still drop `scenarios/**`
    via the second-pass filter.
    """
    inc = include_dirs or _DEFAULT_INCLUDE_DIRS
    exc = exclude_globs or _DEFAULT_EXCLUDE_GLOBS
    out: list[Path] = []

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _DATA_EXTENSIONS:
            continue
        try:
            rel_parts = p.relative_to(root).as_posix().split("/")
        except ValueError:
            continue
        if not rel_parts or rel_parts[0] not in inc:
            continue

        # Drop if any path component is in the ignore set — catches
        # `data/node_modules/...` and `data/.venv/...` at any depth.
        if any(part in _IGNORE_DIR_NAMES for part in rel_parts):
            continue

        rel = "/".join(rel_parts)
        if any(fnmatch.fnmatch(rel, g) for g in exc):
            continue

        out.append(p)

    return out


def read_data_file(path: Path, root: Path) -> DataFileEntry | None:
    """Read a candidate file. Returns None on size-cap or read error.

    UTF-8 with `errors='replace'` — we want best-effort decoding so a
    config file with stray bytes still gets indexed. The downstream
    LLM is robust to occasional replacement chars.
    """
    try:
        size = path.stat().st_size
    except OSError as e:
        log.debug("data file %s: stat failed (%s)", path, e)
        return None
    if size > MAX_FILE_BYTES:
        log.info(
            "skipping %s — %d bytes > %d cap",
            path, size, MAX_FILE_BYTES,
        )
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.debug("data file %s: read failed (%s)", path, e)
        return None
    rel = path.relative_to(root).as_posix()
    return DataFileEntry(
        path=path,
        rel_path=rel,
        content=text,
        bytes_size=size,
    )
