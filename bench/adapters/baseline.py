from pathlib import Path

from adapters.base import AbstractGraphAdapter


class BaselineAdapter(AbstractGraphAdapter):
    name = "Baseline (no graph)"
    short_id = "BL"
    version = "n/a"

    def install(self) -> None:
        pass

    def init_graph(self, project_path: Path) -> None:
        # The source invoicekit now ships with .claude/settings.json wired to
        # winkers hooks (for WNK runs). Those hooks reference winkers state
        # (.winkers/, MCP server) that doesn't exist in a baseline run, so the
        # UserPromptSubmit prompt-enrich hook fails and Claude Code silently
        # aborts the session (observed: 0 turns, 0 tokens, empty result).
        # Strip the settings file so baseline is truly baseline.
        settings = project_path / ".claude" / "settings.json"
        if settings.exists():
            settings.unlink()

    def get_mcp_json(self) -> dict:
        return {}

    def get_allowed_tools(self) -> list[str]:
        return []

    def get_prompt_addendum(self) -> str:
        return ""
