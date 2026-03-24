"""Data models for the Winkers dependency graph."""

from pydantic import BaseModel


class Param(BaseModel):
    name: str
    type_hint: str | None = None
    default: str | None = None


class CallSite(BaseModel):
    caller_fn_id: str
    file: str
    line: int
    expression: str


class FunctionNode(BaseModel):
    id: str                         # "modules/pricing.py::calculate_price"
    file: str
    name: str
    kind: str                       # function | method | arrow | lambda
    language: str
    line_start: int
    line_end: int
    params: list[Param]
    return_type: str | None = None
    is_exported: bool = False
    is_async: bool = False
    docstring: str | None = None
    complexity: int = 0
    lines: int = 0


class FileNode(BaseModel):
    path: str
    language: str
    imports: list[dict]             # [{source, names, alias}]
    function_ids: list[str]
    lines_of_code: int = 0
    zone: str | None = None


class CallEdge(BaseModel):
    source_fn: str
    target_fn: str
    call_site: CallSite
    confidence: float = 1.0         # 1.0=direct, 0.9=module/relative, 0.5=name guess


class ImportEdge(BaseModel):
    source_file: str
    target_file: str
    names: list[str]


class Graph(BaseModel):
    files: dict[str, FileNode] = {}
    functions: dict[str, FunctionNode] = {}
    call_edges: list[CallEdge] = []
    import_edges: list[ImportEdge] = []
    meta: dict = {}

    def is_locked(self, fn_id: str) -> bool:
        """Function is locked if it has incoming call edges."""
        return any(e.target_fn == fn_id for e in self.call_edges)

    def callers(self, fn_id: str) -> list[CallEdge]:
        """All call edges where this function is the target."""
        return [e for e in self.call_edges if e.target_fn == fn_id]

    def callees(self, fn_id: str) -> list[CallEdge]:
        """All call edges where this function is the source."""
        return [e for e in self.call_edges if e.source_fn == fn_id]

    def locked_functions(self) -> list[FunctionNode]:
        """All functions that have at least one incoming call edge."""
        locked_ids = {e.target_fn for e in self.call_edges}
        return [fn for fn in self.functions.values() if fn.id in locked_ids]
