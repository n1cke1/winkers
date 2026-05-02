"""Tests for Claude Code hooks — stdin/stdout JSON protocol."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from winkers.graph import GraphBuilder
from winkers.hooks.prompt_enrich import extract_intent, has_creation_intent
from winkers.resolver import CrossFileResolver
from winkers.session.state import SessionState, SessionStore, Warning, WriteEvent
from winkers.store import GraphStore

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# prompt_enrich — creation intent detection
# ---------------------------------------------------------------------------


class TestCreationIntent:
    def test_detects_add_function(self):
        assert has_creation_intent("add a function to validate email")

    def test_detects_create_class(self):
        assert has_creation_intent("create a new class for user authentication")

    def test_detects_implement_method(self):
        assert has_creation_intent("implement a method to parse CSV config")

    def test_detects_write_handler(self):
        assert has_creation_intent("write a new handler for the /api/users endpoint")

    def test_no_intent_on_fix(self):
        assert not has_creation_intent("fix the bug in calculate_price")

    def test_no_intent_on_refactor(self):
        assert not has_creation_intent("refactor the database connection logic")

    def test_no_intent_on_explain(self):
        assert not has_creation_intent("explain how the auth middleware works")

    def test_extract_intent_cleans_noise(self):
        result = extract_intent("please can you add a function to calculate price")
        assert "calculate price" in result
        assert "please" not in result

    def test_extract_intent_truncates(self):
        long_prompt = "add a function " + "x" * 300
        result = extract_intent(long_prompt)
        assert len(result) <= 200


# ---------------------------------------------------------------------------
# prompt_enrich — full hook flow
# ---------------------------------------------------------------------------


class TestPromptEnrichHook:
    def test_no_creation_intent_exits_silently(self, graph, tmp_path):
        """Non-creation prompt → exit 0, no output."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        hook_input = json.dumps({
            "session_id": "test",
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "user_prompt": "fix the bug in pricing module",
        })

        from winkers.hooks.prompt_enrich import run
        with patch("sys.stdin") as mock_stdin, \
             pytest.raises(SystemExit) as exc_info:
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
        assert exc_info.value.code == 0

    def test_creation_intent_with_matches(self, graph, tmp_path):
        """Creation prompt with matching functions → additionalContext."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        hook_input = json.dumps({
            "session_id": "test",
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "user_prompt": "add a function to calculate price with discount",
        })

        from winkers.hooks.prompt_enrich import run
        captured_output = []
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit") as mock_exit, \
             patch("builtins.print", side_effect=lambda x: captured_output.append(x)):
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
            mock_exit.assert_called_with(0)

        if captured_output:
            result = json.loads(captured_output[0])
            assert "hookSpecificOutput" in result
            ctx = result["hookSpecificOutput"]["additionalContext"]
            assert "Winkers" in ctx
            assert "existing" in ctx.lower() or "implementations" in ctx.lower()

    def test_cyrillic_prompt_emits_translation_section(self, graph, tmp_path):
        """Russian prompt → translator runs, English form injected as context."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        hook_input = json.dumps({
            "session_id": "test",
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "user_prompt": "упростить статусы инвойсов с 6 до 3",
        })

        from winkers.hooks.prompt_enrich import run
        captured_output = []
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit") as mock_exit, \
             patch(
                 "winkers.descriptions.translator._run_translate",
                 return_value="simplify invoice statuses from 6 to 3",
             ), \
             patch(
                 "builtins.print",
                 side_effect=lambda x: captured_output.append(x),
             ):
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
            mock_exit.assert_called_with(0)

        assert captured_output, "expected the hook to emit additionalContext"
        result = json.loads(captured_output[0])
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert "translated to English" in ctx
        assert "simplify invoice statuses from 6 to 3" in ctx

    def test_english_prompt_no_translation_section(self, graph, tmp_path):
        """Pure-English prompt → translator skipped, no translation block."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        hook_input = json.dumps({
            "session_id": "test",
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "user_prompt": "explain the calculate_price function",
        })

        from winkers.hooks.prompt_enrich import run
        captured_output = []
        # _run_translate must NEVER be called for English input.
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit"), \
             patch(
                 "winkers.descriptions.translator._run_translate",
             ) as mock_run, \
             patch(
                 "builtins.print",
                 side_effect=lambda x: captured_output.append(x),
             ):
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
        mock_run.assert_not_called()
        # No output is fine — explain prompts emit no sections.
        for raw in captured_output:
            assert "translated to English" not in raw


# ---------------------------------------------------------------------------
# post_write — file update hook
# ---------------------------------------------------------------------------


class TestPostWriteHook:
    def test_non_code_file_exits_silently(self, tmp_path):
        """Write to .md file → exit 0, no output."""
        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "README.md")},
        })

        from winkers.hooks.post_write import run
        with patch("sys.stdin") as mock_stdin, \
             pytest.raises(SystemExit) as exc_info:
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
        assert exc_info.value.code == 0

    def test_code_file_updates_session(self, graph, tmp_path):
        """Write to .py file → session state updated."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        # Create the source file so update_files can reparse it
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        src = PYTHON_FIXTURE / "modules" / "pricing.py"
        (modules_dir / "pricing.py").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )

        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "modules" / "pricing.py")},
        })

        from winkers.hooks.post_write import run
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit") as mock_exit:
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
            mock_exit.assert_called_with(0)

        # Check session state was created
        session = SessionStore(tmp_path).load()
        assert session is not None
        assert len(session.writes) >= 1

    def test_skips_non_write_tools(self, tmp_path):
        """Non-Write tool → exit 0, no action."""
        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
        })

        from winkers.hooks.post_write import run
        with patch("sys.stdin") as mock_stdin, \
             pytest.raises(SystemExit) as exc_info:
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# session_audit — stop hook
# ---------------------------------------------------------------------------


class TestSessionAuditHook:
    def test_no_session_exits_silently(self, graph, tmp_path):
        """No session → exit 0, allow stop."""
        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "Stop",
        })

        from winkers.hooks.session_audit import run
        with patch("sys.stdin") as mock_stdin, \
             pytest.raises(SystemExit) as exc_info:
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
        assert exc_info.value.code == 0

    def test_clean_session_emits_pass_status(self, graph, tmp_path):
        """Wave 6 — clean session → PASS in additionalContext, no force-continue."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        session = SessionState(started_at="2026-01-01T00:00:00Z")
        session.add_write(WriteEvent(timestamp="t1", file_path="a.py"))
        session.before_create_calls = 1  # avoid the no_intent WARN
        SessionStore(tmp_path).save(session)

        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "Stop",
        })

        captured_output = []
        from winkers.hooks.session_audit import run
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit") as mock_exit, \
             patch("builtins.print", side_effect=lambda x: captured_output.append(x)):
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
            mock_exit.assert_called_with(0)

        assert captured_output
        result = json.loads(captured_output[0])
        # No `continue` key — Wave 6 dropped force-continuation entirely.
        assert "continue" not in result
        assert (
            "PASS"
            in result["hookSpecificOutput"]["additionalContext"]
        )

    def test_fail_does_not_force_continue(self, graph, tmp_path):
        """Wave 6 — broken callers produce FAIL but Stop still exits cleanly."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        session = SessionState(started_at="2026-01-01T00:00:00Z")
        session.add_write(WriteEvent(timestamp="t1", file_path="modules/pricing.py"))
        session.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="modules/pricing.py::calculate_price",
            detail="calculate_price() sig changed. 2 callers.",
        ))
        SessionStore(tmp_path).save(session)

        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "Stop",
        })

        captured_output = []
        from winkers.hooks.session_audit import run
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit") as mock_exit, \
             patch("builtins.print", side_effect=lambda x: captured_output.append(x)):
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
            mock_exit.assert_called_with(0)

        assert captured_output
        result = json.loads(captured_output[0])
        # No force-continue: FAIL is informational.
        assert "continue" not in result
        assert "FAIL" in result["hookSpecificOutput"]["additionalContext"]
        # audit.json + pending_audit.md were persisted for next session.
        from winkers.session.audit import (
            AUDIT_FILENAME,
            PENDING_AUDIT_FILENAME,
        )
        from winkers.session.session_dir import get_session_dir
        sess_dir = get_session_dir(tmp_path, "test")
        assert (sess_dir / AUDIT_FILENAME).exists()
        assert (tmp_path / PENDING_AUDIT_FILENAME).exists()

    def test_repeat_stop_returns_same_verdict(self, graph, tmp_path):
        """Wave 6 — second Stop returns the same verdict (anti-loop dropped)."""
        (tmp_path / ".winkers").mkdir()
        GraphStore(tmp_path).save(graph)

        session = SessionState(started_at="2026-01-01T00:00:00Z")
        session.add_write(WriteEvent(timestamp="t1", file_path="modules/pricing.py"))
        session.add_warning(Warning(
            kind="broken_caller", severity="error",
            target="modules/pricing.py::calculate_price",
            detail="sig changed",
        ))
        # Simulate first call already happened
        session.session_done_calls = 1
        SessionStore(tmp_path).save(session)

        hook_input = json.dumps({
            "session_id": "test",
            "hook_event_name": "Stop",
        })

        captured_output = []
        from winkers.hooks.session_audit import run
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.exit") as mock_exit, \
             patch("builtins.print", side_effect=lambda x: captured_output.append(x)):
            mock_stdin.read.return_value = hook_input
            run(tmp_path)
            mock_exit.assert_called_with(0)

        assert captured_output
        result = json.loads(captured_output[0])
        # No `continue` either way — second call still surfaces FAIL,
        # but the hook never blocks Stop.
        assert "continue" not in result


# ---------------------------------------------------------------------------
# Hook installer
# ---------------------------------------------------------------------------


class TestHookInstaller:
    def test_install_interactive_hooks(self, tmp_path):
        """_install_interactive_hooks adds 3 hook events (Stop removed in 0.8.1)."""
        from winkers.cli.main import _install_interactive_hooks

        hooks: dict = {}
        changed = _install_interactive_hooks(hooks, "winkers", tmp_path)

        assert changed is True
        assert "UserPromptSubmit" in hooks
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks
        # Stop hook was deliberately removed in 0.8.1 (session_done muted).
        assert "Stop" not in hooks

        # Check matcher on PreToolUse
        pre_tool = hooks["PreToolUse"][0]
        assert pre_tool["matcher"] == "Write|Edit|MultiEdit"

    def test_install_idempotent(self, tmp_path):
        """Running installer twice doesn't duplicate hooks."""
        from winkers.cli.main import _install_interactive_hooks

        hooks: dict = {}
        _install_interactive_hooks(hooks, "winkers", tmp_path)
        changed = _install_interactive_hooks(hooks, "winkers", tmp_path)

        assert changed is False
        assert len(hooks["UserPromptSubmit"]) == 1
        assert len(hooks["PostToolUse"]) == 1

    def test_install_updates_stale_path(self, tmp_path):
        """Hooks with old path get updated to new winkers binary."""
        from winkers.cli.main import _install_interactive_hooks

        hooks: dict = {}
        # Install with old Linux path
        _install_interactive_hooks(hooks, "/opt/old/.venv/bin/winkers", tmp_path)
        old_cmd = hooks["PostToolUse"][0]["hooks"][0]["command"]
        assert "/opt/old/" in old_cmd

        # Re-install with current Windows path
        changed = _install_interactive_hooks(
            hooks, "C:/Dev/.venv/Scripts/winkers", tmp_path,
        )
        assert changed is True
        new_cmd = hooks["PostToolUse"][0]["hooks"][0]["command"]
        assert "C:/Dev/" in new_cmd
        assert "/opt/old/" not in new_cmd
        # No duplicates
        assert len(hooks["PostToolUse"]) == 1

    def test_install_removes_legacy_session_audit(self, tmp_path):
        """0.8.1: any pre-existing Stop/session-audit hook is stripped on install."""
        from winkers.cli.main import _install_interactive_hooks

        hooks: dict = {
            "Stop": [
                {"hooks": [{
                    "type": "command",
                    "command": "/old/winkers hook session-audit /proj",
                    "timeout": 10,
                }]},
            ],
        }
        _install_interactive_hooks(hooks, "winkers", tmp_path)
        assert "Stop" not in hooks

    def test_upsert_session_hook(self):
        """_upsert_hook updates existing command path."""
        from winkers.cli.main import _upsert_hook

        hook_list: list[dict] = [{
            "matcher": "",
            "hooks": [{"type": "command", "command": "/old/path/winkers record --hook"}],
        }]
        changed = _upsert_hook(
            hook_list, "record", "/new/path/winkers record --hook",
            label="test",
        )
        assert changed is True
        assert hook_list[0]["hooks"][0]["command"] == "/new/path/winkers record --hook"
        assert len(hook_list) == 1  # no duplicate
