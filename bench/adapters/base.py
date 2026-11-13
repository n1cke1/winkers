from abc import ABC, abstractmethod
from pathlib import Path


class AbstractGraphAdapter(ABC):
    name: str
    short_id: str
    version: str = "unknown"

    @abstractmethod
    def install(self) -> None:
        pass

    @abstractmethod
    def init_graph(self, project_path: Path) -> None:
        pass

    @abstractmethod
    def get_mcp_json(self) -> dict:
        pass

    @abstractmethod
    def get_allowed_tools(self) -> list[str]:
        pass

    @abstractmethod
    def get_prompt_addendum(self) -> str:
        pass

    def teardown(self, project_path: Path) -> None:
        pass

    def save_artifacts(self, project_path: Path, run_dir: Path) -> None:
        """Preserve adapter-specific artifacts to run_dir before cleanup.

        Called after Claude Code exits but BEFORE teardown/tmpdir cleanup.
        Adapters that generate their own traces (winkers sessions, CBM
        detailed logs, etc.) can copy them into run_dir to keep a second
        lens on the run alongside claude_output.json.

        Default: no-op.
        """
        pass

    def get_init_cost_estimate(self) -> float:
        return 0.0

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.short_id}>"
