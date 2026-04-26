"""MCP server entry point."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server

from winkers.mcp.tools import register_tools
from winkers.models import Graph
from winkers.store import GraphStore


def create_server(root: Path) -> Server:
    server = Server("winkers")
    store = GraphStore(root)

    state: dict = {
        "graph": store.load(),
        "mtime": store.graph_path.stat().st_mtime if store.exists() else 0.0,
    }

    def get_graph() -> Graph | None:
        # Reload if graph.json was updated (e.g. by winkers init)
        if store.exists():
            current_mtime = store.graph_path.stat().st_mtime
            if current_mtime > state["mtime"]:
                state["graph"] = store.load()
                state["mtime"] = current_mtime
        return state["graph"]

    register_tools(server, root, get_graph)
    _maybe_preload_embeddings(root)
    return server


def _maybe_preload_embeddings(root: Path) -> None:
    """Warm BGE-M3 in a daemon thread when a units index is present.

    Without this, the first find_work_area call pays a 5-15s cold start
    while the agent waits. Skipped when no index exists so projects that
    don't use semantic search don't pay ~2 GiB of RAM for nothing.
    Daemon-flagged so the process can exit instantly even mid-load.
    """
    import threading

    from winkers.embeddings import INDEX_FILENAME
    from winkers.embeddings.builder import preload_model

    if not (root / ".winkers" / INDEX_FILENAME).exists():
        return
    threading.Thread(target=preload_model, daemon=True, name="bge-preload").start()


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
        """Read one non-empty line and return immediately.

        anyio calls read(8192) but messages are ~200 bytes.
        We must not block waiting to fill the buffer — read one
        line and return what we have.
        """
        if not self._buf:
            line = self.readline()
            if not line:
                return b""
            self._buf = line
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
