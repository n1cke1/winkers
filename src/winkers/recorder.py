"""Parse Claude Code transcript.jsonl into a SessionRecord."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from winkers.models import SessionRecord, ToolCall

EXPLORATION_TOOLS = {"Read", "Grep", "Glob", "LS", "Bash"}
MODIFICATION_TOOLS = {"Write", "Edit", "MultiEdit"}
VERIFICATION_COMMANDS = re.compile(
    r"\b(pytest|py\.test|npm\s+test|cargo\s+test|go\s+test|dotnet\s+test|jest|vitest|mocha)\b"
)
TEST_PASS_PATTERN = re.compile(r"(\d+)\s+passed")
TEST_FAIL_PATTERN = re.compile(r"(\d+)\s+failed|FAILED|FAIL\b|error\b", re.IGNORECASE)

WINKERS_TOOL_PREFIX = "mcp__winkers__"


def parse_transcript(path: Path) -> SessionRecord:
    """Read a transcript.jsonl and return a SessionRecord."""
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    return _build_record(entries)


def parse_transcript_text(text: str) -> SessionRecord:
    """Parse transcript from raw text (for stdin/hook usage)."""
    entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    return _build_record(entries)


def _build_record(entries: list[dict]) -> SessionRecord:
    session_id = ""
    model = ""
    task_prompt = ""
    started_at = ""
    completed_at = ""
    tool_calls: list[ToolCall] = []
    files_read: set[str] = set()
    files_modified: set[str] = set()
    files_created: set[str] = set()
    winkers_calls: dict[str, int] = {}
    user_corrections: list[str] = []
    tests_passed: bool | None = None
    exploration = 0
    modification = 0
    verification = 0
    total_turns = 0
    first_human_seen = False

    for entry in entries:
        etype = entry.get("type", "")

        if not session_id:
            session_id = entry.get("sessionId", "")
        if not started_at and entry.get("timestamp"):
            started_at = entry["timestamp"]
        if entry.get("timestamp"):
            completed_at = entry["timestamp"]

        if etype == "user":
            _process_user(
                entry, first_human_seen, task_prompt,
                user_corrections, files_read, files_modified,
            )
            # Extract task_prompt from first human text
            if not first_human_seen and not task_prompt:
                prompt = _extract_human_text(entry)
                if prompt:
                    task_prompt = prompt
                    first_human_seen = True

        elif etype == "assistant":
            msg = entry.get("message", {})
            if not model:
                model = msg.get("model", "")

            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue

                total_turns += 1
                tc = _extract_tool_call(block, entry)
                tool_calls.append(tc)

                category = _classify_tool(tc.name, tc.input_params)
                if category == "exploration":
                    exploration += 1
                elif category == "modification":
                    modification += 1
                elif category == "verification":
                    verification += 1

                _track_files(tc, files_read, files_modified, files_created)
                _track_winkers(tc, winkers_calls)

    # Detect test results from verification bash calls
    tests_passed = _detect_test_results(entries)

    session_end = _detect_session_end(entries)

    task_hash = hashlib.sha256(task_prompt.encode()).hexdigest()[:12]

    return SessionRecord(
        session_id=session_id,
        started_at=started_at,
        completed_at=completed_at,
        model=model,
        task_prompt=task_prompt,
        task_hash=task_hash,
        tool_calls=tool_calls,
        total_turns=total_turns,
        exploration_turns=exploration,
        modification_turns=modification,
        verification_turns=verification,
        files_read=sorted(files_read),
        files_modified=sorted(files_modified),
        files_created=sorted(files_created),
        tests_passed=tests_passed,
        winkers_calls=winkers_calls,
        user_corrections=user_corrections,
        session_end=session_end,
    )


def _extract_human_text(entry: dict) -> str:
    """Get plain text from a user message (skip tool_result blocks)."""
    for block in entry.get("message", {}).get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "").strip()
            if text and len(text) > 2:
                return text
    return ""


def _process_user(
    entry: dict,
    first_human_seen: bool,
    task_prompt: str,
    user_corrections: list[str],
    files_read: set[str],
    files_modified: set[str],
) -> None:
    """Process a user-type entry for corrections and tool results."""
    if first_human_seen:
        text = _extract_human_text(entry)
        if text and _looks_like_correction(text):
            user_corrections.append(text[:200])


def _looks_like_correction(text: str) -> bool:
    """Heuristic: does this user message correct the agent?"""
    lower = text.lower()
    patterns = [
        "no ", "no,", "don't", "dont", "stop", "wrong", "instead",
        "not that", "undo", "revert", "why did you", "that's not",
    ]
    return any(p in lower for p in patterns)


def _extract_tool_call(block: dict, entry: dict) -> ToolCall:
    usage = entry.get("message", {}).get("usage", {})
    return ToolCall(
        name=block.get("name", ""),
        input_params=block.get("input", {}),
        tokens_in=usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0),
        tokens_out=usage.get("output_tokens", 0),
        timestamp=entry.get("timestamp", ""),
    )


def _classify_tool(name: str, params: dict) -> str:
    """Classify a tool call as exploration, modification, or verification."""
    if name.startswith(WINKERS_TOOL_PREFIX):
        return "exploration"

    if name in MODIFICATION_TOOLS:
        return "modification"

    if name == "Bash":
        cmd = params.get("command", "")
        if VERIFICATION_COMMANDS.search(cmd):
            return "verification"
        return "exploration"

    if name in EXPLORATION_TOOLS:
        return "exploration"

    return "exploration"


def _track_files(
    tc: ToolCall,
    files_read: set[str],
    files_modified: set[str],
    files_created: set[str],
) -> None:
    """Track which files were read, modified, or created."""
    name = tc.name
    params = tc.input_params

    if name == "Read":
        path = params.get("file_path", "")
        if path:
            files_read.add(_normalize_path(path))

    elif name == "Edit":
        path = params.get("file_path", "")
        if path:
            files_modified.add(_normalize_path(path))

    elif name == "Write":
        path = params.get("file_path", "")
        if path:
            files_created.add(_normalize_path(path))

    elif name == "MultiEdit":
        path = params.get("file_path", "")
        if path:
            files_modified.add(_normalize_path(path))


def _normalize_path(path: str) -> str:
    """Normalize to forward slashes, strip drive letter prefix."""
    path = path.replace("\\", "/")
    if len(path) > 2 and path[1] == ":":
        path = path[2:]
    return path


def _track_winkers(tc: ToolCall, winkers_calls: dict[str, int]) -> None:
    if tc.name.startswith(WINKERS_TOOL_PREFIX):
        short = tc.name[len(WINKERS_TOOL_PREFIX):]
        winkers_calls[short] = winkers_calls.get(short, 0) + 1


def _detect_test_results(entries: list[dict]) -> bool | None:
    """Scan tool results for test pass/fail signals."""
    last_result: bool | None = None

    for entry in entries:
        if entry.get("type") != "user":
            continue
        result = entry.get("toolUseResult", {})
        if not isinstance(result, dict):
            continue
        stdout = result.get("stdout", "")
        if not stdout:
            continue

        if not VERIFICATION_COMMANDS.search(stdout) and not _is_test_result(stdout):
            continue

        if TEST_FAIL_PATTERN.search(stdout):
            last_result = False
        elif TEST_PASS_PATTERN.search(stdout):
            last_result = True

    return last_result


def _is_test_result(text: str) -> bool:
    """Check if output looks like test results."""
    return bool(TEST_PASS_PATTERN.search(text)) or "test session starts" in text.lower()


def _detect_session_end(entries: list[dict]) -> str:
    """Determine how the session ended."""
    # Walk backwards to find last assistant message
    for entry in reversed(entries):
        if entry.get("type") == "assistant":
            stop = entry.get("message", {}).get("stop_reason", "")
            if stop == "end_turn":
                return "agent_done"
            if stop == "max_tokens":
                return "max_turns"
            break
    return "user_killed"


def find_project_transcripts(project_path: Path) -> list[Path]:
    """Find all transcript files for a project in ~/.claude/projects/.

    Instead of guessing Claude Code's path encoding, scan all project
    directories and match by reading the cwd field from transcripts.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return []

    target = str(project_path).replace("\\", "/").lower().rstrip("/")
    result: list[Path] = []

    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            if _transcript_matches_project(jsonl, target):
                result.append(jsonl)

    return sorted(result)


def _transcript_matches_project(jsonl_path: Path, target_lower: str) -> bool:
    """Check if a transcript belongs to the target project by reading cwd."""
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                cwd = entry.get("cwd", "")
                if cwd:
                    cwd_norm = cwd.replace("\\", "/").lower().rstrip("/")
                    return cwd_norm == target_lower
                # Also check inside message for user entries
                if entry.get("type") == "user" and entry.get("cwd"):
                    cwd_norm = entry["cwd"].replace("\\", "/").lower().rstrip("/")
                    return cwd_norm == target_lower
    except Exception:
        pass
    return False
