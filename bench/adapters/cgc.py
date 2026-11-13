import shutil
import subprocess
from pathlib import Path

from adapters.base import AbstractGraphAdapter


class CodeGraphContextAdapter(AbstractGraphAdapter):
    name = "CodeGraphContext"
    short_id = "CGC"
    version = "unknown"

    def install(self) -> None:
        if not shutil.which("cgc"):
            raise RuntimeError(
                "cgc CLI not found. Install: pip install codegraphcontext"
            )
        result = subprocess.run(
            ["cgc", "--version"], capture_output=True, text=True,
        )
        if result.returncode == 0:
            out = (result.stdout or result.stderr).strip()
            self.version = out.split()[-1] if out else "unknown"

    def init_graph(self, project_path: Path) -> None:
        # CGC uses global KùzuDB/FalkorDB, indexing done once out-of-band.
        # Strip winkers hooks that leak in from source .claude/settings.json.
        settings = project_path / ".claude" / "settings.json"
        if settings.exists():
            settings.unlink()

    def get_mcp_json(self) -> dict:
        return {
            "mcpServers": {
                "cgc": {
                    "command": "cgc",
                    "args": ["mcp", "start"],
                }
            }
        }

    def get_allowed_tools(self) -> list[str]:
        return [
            "mcp__cgc__add_code_to_graph",
            "mcp__cgc__find_code",
            "mcp__cgc__analyze_code_relationships",
            "mcp__cgc__find_dead_code",
            "mcp__cgc__execute_cypher_query",
        ]

    def get_prompt_addendum(self) -> str:
        return (
            "This project has CodeGraphContext MCP tools available for code analysis.\n"
            "You can use them to understand the codebase:\n"
            "- mcp__cgc__find_code: search for symbols by name or pattern\n"
            "- mcp__cgc__analyze_code_relationships: callers, callees, class hierarchies\n"
            "- mcp__cgc__execute_cypher_query: complex structural queries"
        )

    def teardown(self, project_path: Path) -> None:
        pass
