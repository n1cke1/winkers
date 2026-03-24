"""Base class for language profiles."""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class LanguageProfile(ABC):
    language: str
    extensions: list[str]
    tree_sitter_language: str

    # Tree-sitter S-expression queries
    function_query: str
    call_query: str
    import_query: str
    export_query: str | None = None
    class_query: str | None = None

    def resolve_import(self, import_node: dict, project_files: list[str]) -> str | None:
        """Resolve an import to an absolute file path. Override per language."""
        return None

    def is_exported(self, function_name: str, file_imports: list[dict]) -> bool:
        """Determine if a function is exported. Override per language."""
        return False
