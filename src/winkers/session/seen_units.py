"""In-memory ``seen_units`` registry — Wave 7 context dedup.

Tracks which unit ids have already been returned to the agent (with
their description) and by which tool, so subsequent tool calls can
suppress the heavy ``description`` field and surface a marker like
``description_seen_in: "find_work_area@call#7"`` instead.

Why in-memory rather than per-session JSON like ``hooks.log``?
The MCP protocol doesn't pass Claude's session_id to tool calls — only
hooks receive it. ``winkers serve`` is a single long-running process
attached to one project; there's typically one Claude session at a
time hitting it. An in-memory dict reset on MCP server restart is the
right granularity. If two Claude sessions ever share the same MCP
server (rare), they'd share dedup state — acceptable for an
information-only optimization.

Suppression rule
----------------
A unit is "recently seen" iff
    (current_call_idx - seen_call_idx) < THRESHOLD
where ``current_call_idx`` is the registry's monotonic counter at the
time the second tool's response is being formatted. THRESHOLD defaults
to 10 tool calls — a guess at when context compaction starts dropping
older results from the agent's window.

Only ``description`` (the heavy paragraph) gets suppressed. ``summary``,
``risk_level``, ``callers_classification`` etc. always travel — they're
small and per-call-context useful even on repeat hits.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

DEFAULT_THRESHOLD = 10


@dataclass
class SeenEntry:
    tool: str
    call_idx: int
    ts: str

    def marker(self) -> str:
        """Human-readable hint used in the suppression placeholder."""
        return f"{self.tool}@call#{self.call_idx}"


@dataclass
class SeenUnitsRegistry:
    """Per-MCP-server-process unit visibility tracker.

    Threadsafe — the MCP server uses asyncio but tool handlers can run
    on the default executor; the lock guards all mutations.
    """

    threshold: int = DEFAULT_THRESHOLD
    _seen: dict[str, SeenEntry] = field(default_factory=dict)
    _next_call_idx: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _instance: ClassVar[SeenUnitsRegistry | None] = None

    @classmethod
    def get(cls) -> SeenUnitsRegistry:
        """Module-level singleton — one registry per MCP server process."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton — used by tests + on MCP server restart."""
        cls._instance = None

    # ------------------------------------------------------------------

    def begin_call(self, tool: str) -> int:
        """Reserve a fresh `call_idx` for a tool invocation.

        The returned idx must be passed to `record` so all units
        recorded during the same tool call share one idx (matters for
        the suppression boundary check).
        """
        with self._lock:
            idx = self._next_call_idx
            self._next_call_idx += 1
            return idx

    def record(self, unit_ids: list[str], tool: str, call_idx: int) -> None:
        """Mark `unit_ids` as recently shown by `tool` at `call_idx`."""
        if not unit_ids:
            return
        ts = datetime.now(UTC).isoformat()
        with self._lock:
            for uid in unit_ids:
                if not uid:
                    continue
                self._seen[uid] = SeenEntry(tool=tool, call_idx=call_idx, ts=ts)

    def is_recently_seen(self, unit_id: str) -> bool:
        """True iff `unit_id` was recorded within the last `threshold` calls."""
        return self.recent_marker(unit_id) is not None

    def recent_marker(self, unit_id: str) -> str | None:
        """Return the `tool@call#N` marker for `unit_id`, or None."""
        with self._lock:
            entry = self._seen.get(unit_id)
            if entry is None:
                return None
            current = self._next_call_idx
            if (current - entry.call_idx) >= self.threshold:
                return None
            return entry.marker()

    # ------------------------------------------------------------------
    # Suppression helper used by tools formatting `description` fields.
    # ------------------------------------------------------------------

    def maybe_suppress_description(self, unit_id: str, item: dict) -> dict:
        """If `unit_id` was recently shown, replace `description` with a marker.

        Returns the (possibly mutated) item dict for chained-style use.
        Mutates ``item`` in place: removes the ``description`` key and
        adds ``description_seen_in`` when suppression fires.
        """
        marker = self.recent_marker(unit_id)
        if marker is None:
            return item
        if "description" in item:
            del item["description"]
        item["description_seen_in"] = marker
        return item


def reset_for_tests() -> None:
    """Public test helper — clears the singleton + counter."""
    SeenUnitsRegistry.reset()
