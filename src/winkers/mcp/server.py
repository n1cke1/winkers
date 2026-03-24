"""MCP server entry point."""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server

from winkers.mcp.tools import register_tools
from winkers.models import Graph
from winkers.store import GraphStore

# Extensions to scan for auto-rebuild
_SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".go", ".rs", ".cs",
}


def _latest_source_mtime(root: Path) -> float:
    """Return the most recent mtime among source files."""
    latest = 0.0
    for p in root.rglob("*"):
        if p.suffix in _SOURCE_EXTS and ".venv" not in p.parts and "node_modules" not in p.parts:
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                pass
    return latest


def create_server(root: Path) -> Server:
    server = Server("winkers")
    store = GraphStore(root)

    state: dict = {
        "graph": store.load(),
        "built_at": store.graph_path.stat().st_mtime if store.exists() else 0.0,
    }

    def _maybe_rebuild() -> Graph | None:
        """Rebuild graph if any source file is newer than last build."""
        latest = _latest_source_mtime(root)
        if latest > state["built_at"] and state["graph"] is not None:
            from winkers.graph import GraphBuilder
            from winkers.resolver import CrossFileResolver

            graph = GraphBuilder().build(root)
            CrossFileResolver().resolve(graph, str(root))
            store.save(graph)
            state["graph"] = graph
            state["built_at"] = time.time()
        return state["graph"]

    def get_graph() -> Graph | None:
        return _maybe_rebuild()

    register_tools(server, root, get_graph)
    return server


class _FilteredStdin(io.RawIOBase):
    """Wraps stdin.buffer, stripping bare newlines that break JSON-RPC.

    Claude Code sends \\n between messages. anyio on Windows reads via
    read(8192), not readline(). So we must filter in read() and readinto().
    """

    def __init__(self, raw: io.RawIOBase) -> None:
        self._raw = raw
        self._buf = b""

    def readline(self, size: int = -1) -> bytes:
        """Read one non-empty line from raw stdin."""
        while True:
            line = self._raw.readline(size)
            if not line:  # EOF
                return line
            if line.strip():  # non-empty
                return line
            # empty line — skip

    def read(self, size: int = -1) -> bytes:
        """Read by accumulating non-empty lines into buffer."""
        while len(self._buf) < (size if size > 0 else 1):
            line = self.readline()
            if not line:  # EOF
                break
            self._buf += line
        if size <= 0:
            out = self._buf
            self._buf = b""
            return out
        out = self._buf[:size]
        self._buf = self._buf[size:]
        return out

    def readinto(self, b: bytearray) -> int | None:
        """Delegate to read() so empty lines are filtered."""
        data = self.read(len(b))
        if not data:
            return 0
        n = len(data)
        b[:n] = data
        return n

    def readable(self) -> bool:
        return True


def run(root: Path | None = None) -> None:
    if root is None:
        root = Path.cwd()

    # Wrap stdin to filter empty lines before MCP SDK parses JSON-RPC
    sys.stdin = io.TextIOWrapper(_FilteredStdin(sys.stdin.buffer))

    server = create_server(root)

    async def _main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_main())
