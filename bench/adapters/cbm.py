import os
import shutil
import subprocess
from pathlib import Path

from adapters.base import AbstractGraphAdapter

CBM_BIN = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "codebase-memory-mcp", "codebase-memory-mcp.exe",
)


class CodebaseMemoryAdapter(AbstractGraphAdapter):
    name = "codebase-memory-mcp"
    short_id = "CBM"
    version = "unknown"

    def __init__(self) -> None:
        super().__init__()
        # Store project_path from init_graph so get_prompt_addendum knows
        # which CBM project name to tell the agent about.
        self._project_path: Path | None = None

    def _bin(self) -> str:
        if os.path.exists(CBM_BIN):
            return CBM_BIN
        found = shutil.which("codebase-memory-mcp")
        if found:
            return found
        raise RuntimeError(
            "codebase-memory-mcp not found. Install from: "
            "https://github.com/DeusData/codebase-memory-mcp"
        )

    def install(self) -> None:
        b = self._bin()
        result = subprocess.run(
            [b, "--version"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            # Output like "codebase-memory-mcp 0.6.0" — take last token
            self.version = result.stdout.strip().split()[-1]

    def init_graph(self, project_path: Path) -> None:
        # Strip any winkers hooks that leaked in via source .claude/settings.json —
        # they fail on a non-winkers run and silently abort Claude Code. See baseline.py.
        settings = project_path / ".claude" / "settings.json"
        if settings.exists():
            settings.unlink()

        self._project_path = project_path

        # Always index the fresh temp project. CBM 0.6.0 indexes invoicekit
        # in sub-second (~700ms), so the "cache hit" logic that was needed
        # for the old fixed-workdir setup is now just overhead we can drop.
        # Each run gets a clean, fresh index matching the temp path.
        b = self._bin()
        result = subprocess.run(
            [b, "cli", "index_repository",
             f'{{"repo_path": "{project_path.as_posix()}"}}'],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cbm index failed: {result.stderr}")

    def get_mcp_json(self) -> dict:
        return {
            "mcpServers": {
                "codebase-memory-mcp": {
                    "command": CBM_BIN if os.path.exists(CBM_BIN) else "codebase-memory-mcp",
                    "type": "stdio",
                }
            }
        }

    def get_allowed_tools(self) -> list[str]:
        return [
            "mcp__codebase-memory-mcp__search_graph",
            # search_code is known-broken in 0.6.0 on Windows (bundled grep
            # shells out with "path not found" for every pattern including
            # regex .*). Reporting to DeusData/codebase-memory-mcp. Excluding
            # it from the allowlist prevents the agent from wasting turns on
            # a tool that silently returns 0 and produces false "nothing here"
            # signals that lead to catastrophic refactors.
            # "mcp__codebase-memory-mcp__search_code",
            "mcp__codebase-memory-mcp__trace_path",
            "mcp__codebase-memory-mcp__get_code_snippet",
            "mcp__codebase-memory-mcp__query_graph",
            "mcp__codebase-memory-mcp__detect_changes",
            "mcp__codebase-memory-mcp__get_architecture",
            "mcp__codebase-memory-mcp__get_graph_schema",
        ]

    def get_prompt_addendum(self) -> str:
        project_name = self._project_name()
        return (
            "This project has codebase-memory-mcp MCP tools available for code analysis.\n"
            "You can use them to understand the codebase:\n"
            "- mcp__codebase-memory-mcp__get_architecture: high-level project structure and summary\n"
            "- mcp__codebase-memory-mcp__search_graph: find symbols in knowledge graph (BM25 + semantic)\n"
            "- mcp__codebase-memory-mcp__trace_path: who calls/depends on a function\n"
            "- mcp__codebase-memory-mcp__get_code_snippet: read source code by qualified_name\n"
            "- mcp__codebase-memory-mcp__query_graph: Cypher-like queries over the graph\n"
            "- mcp__codebase-memory-mcp__detect_changes: verify impact of modifications\n\n"
            "For text/regex searches across source files, use the built-in Grep tool "
            "(the codebase-memory-mcp `search_code` tool is disabled in this run due to "
            "a known bundling issue on Windows).\n\n"
            f'IMPORTANT: Always pass "project": "{project_name}" in every codebase-memory-mcp call.\n'
            f'Example: trace_path({{\"function_name\": \"my_func\", \"project\": \"{project_name}\"}})'
        )

    def _project_name(self) -> str:
        """Mimic CBM's cbm_project_name_from_path for the current temp project."""
        if self._project_path is None:
            return ""
        path = self._project_path.as_posix()
        name = path.replace("/", "-").replace(":", "")
        while "--" in name:
            name = name.replace("--", "-")
        return name.strip("-")

    def teardown(self, project_path: Path) -> None:
        # Each run creates its own CBM project entry in ~/.cache. Clean it up
        # so the cache directory doesn't grow unboundedly across benchmarks.
        name = self._project_name()
        if not name:
            return
        try:
            subprocess.run(
                [self._bin(), "cli", "delete_project",
                 f'{{"project": "{name}", "confirm": true}}'],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:
            pass  # non-fatal
