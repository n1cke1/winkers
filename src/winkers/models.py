"""Data models for the Winkers dependency graph and session recording."""

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
    route: str | None = None          # "/api/products"
    http_method: str | None = None    # "GET", "POST", etc.
    template: str | None = None       # "products/list.html"
    ast_hash: str | None = None       # normalized AST hash for clone detection
    intent: str | None = None         # LLM-generated one-sentence description


class FileNode(BaseModel):
    path: str
    language: str
    imports: list[dict]             # [{source, names, alias}]
    function_ids: list[str]
    lines_of_code: int = 0
    zone: str | None = None
    recent_commits: list[dict] = []  # [{sha, author, date, message}]


class CallEdge(BaseModel):
    source_fn: str
    target_fn: str
    call_site: CallSite
    confidence: float = 1.0         # 1.0=direct, 0.9=module/relative, 0.5=name guess


class ImportEdge(BaseModel):
    source_file: str
    target_file: str
    names: list[str]


class ValueLockedCollection(BaseModel):
    """A module-level collection of literal values whose membership is read by
    code paths and tested with literal arguments at call sites.

    Detected at indexing time. Drives the `value_locked` warning surface in
    scope / before_create / impact_check — removing a value is a silent
    breaking change for any caller that passes it as a literal.
    """
    name: str                       # "VALID_STATUSES"
    file: str                       # "app/domain/status.py"
    line: int
    kind: str                       # "set", "frozenset", "tuple", "list", "Enum"
    values: list[str]               # only str/int/float, normalized to str
    referenced_by_fns: list[str] = []     # fn_ids whose body references this name
    literal_uses: dict[str, int] = {}     # value → number of caller arg usages
    files_with_uses: list[str] = []       # files where literal usages live


class Graph(BaseModel):
    files: dict[str, FileNode] = {}
    functions: dict[str, FunctionNode] = {}
    call_edges: list[CallEdge] = []
    import_edges: list[ImportEdge] = []
    value_locked_collections: list[ValueLockedCollection] = []
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

    def file_zone(self, path: str) -> str:
        """Return zone for a file path from stored FileNode."""
        fnode = self.files.get(path)
        return fnode.zone if fnode and fnode.zone else "unknown"

    def imports_from_file(self, path: str) -> list["ImportEdge"]:
        """Edges where this file is the source (files it imports)."""
        return [e for e in self.import_edges if e.source_file == path]

    def imported_by_file(self, path: str) -> list["ImportEdge"]:
        """Edges where this file is the target (files importing it)."""
        return [e for e in self.import_edges if e.target_file == path]

    def sibling_imports_count(self, path: str) -> int:
        """Number of files in the same zone imported by this file."""
        zone = self.file_zone(path)
        if zone == "unknown":
            return 0
        count = 0
        for edge in self.imports_from_file(path):
            if edge.target_file == path:
                continue
            if self.file_zone(edge.target_file) == zone:
                count += 1
        return count


# ---------------------------------------------------------------------------
# Session recording models
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    name: str
    input_params: dict = {}
    is_error: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    timestamp: str = ""


class SessionRecord(BaseModel):
    session_id: str
    started_at: str
    completed_at: str
    model: str = ""
    task_prompt: str = ""
    task_hash: str = ""

    tool_calls: list[ToolCall] = []
    total_turns: int = 0
    exploration_turns: int = 0
    modification_turns: int = 0
    verification_turns: int = 0

    files_read: list[str] = []
    files_modified: list[str] = []
    files_created: list[str] = []

    tests_before: int | None = None
    tests_after: int | None = None
    tests_passed: bool | None = None

    winkers_calls: dict[str, int] = {}
    user_corrections: list[str] = []
    session_end: str = "agent_done"


class CommitBinding(BaseModel):
    status: str = "uncommitted"
    hash: str | None = None
    message: str | None = None
    files_changed: list[str] = []
    insertions: int = 0
    deletions: int = 0
    modified_functions: list[str] = []  # fn_ids with changed lines


class DebtDelta(BaseModel):
    complexity_delta: int = 0
    max_function_lines: int = 0
    biggest_file_growth: int = 0
    import_edges_delta: int = 0
    files_created: int = 0
    files_modified: int = 0


class ScoredSession(BaseModel):
    session: SessionRecord
    commit: CommitBinding = CommitBinding()
    debt: DebtDelta = DebtDelta()
    score: float = 0.5
