"""Tests for commit format module."""

from winkers.commit_format import (
    _fallback_message,
    format_message,
    install_hook,
    load_commit_format,
    save_commit_format,
)


def test_save_and_load(tmp_path):
    save_commit_format(tmp_path, "[{ticket}] {message}", r"[A-Z]+-\d+")
    fmt = load_commit_format(tmp_path)
    assert fmt["template"] == "[{ticket}] {message}"
    assert fmt["ticket_pattern"] == r"[A-Z]+-\d+"


def test_load_empty(tmp_path):
    assert load_commit_format(tmp_path) == {}


def test_format_with_ticket():
    result = format_message(
        "PROJ-123 fix login bug",
        "[{ticket}] {message}",
        r"[A-Z]+-\d+",
    )
    assert result == "[PROJ-123] fix login bug"


def test_format_conventional_commit_scope():
    """feat(TICKET): msg → ticket extracted, no empty parens."""
    result = format_message(
        "feat(T-F696C8): SLP message",
        "[{ticket}] {message}",
        r"T-[A-F0-9]+",
    )
    assert result == "[T-F696C8] feat: SLP message"
    assert "()" not in result


def test_format_no_ticket():
    result = format_message(
        "fix login bug",
        "[{ticket}] {message}",
        r"[A-Z]+-\d+",
    )
    # Empty ticket bracket should be cleaned up
    assert "fix login bug" in result
    assert "[]" not in result


def test_format_plain_template():
    result = format_message(
        "fix login bug",
        "{message}",
        r"[A-Z]+-\d+",
    )
    assert result == "fix login bug"


def test_install_hook(tmp_path):
    hook_path = install_hook(tmp_path)
    assert hook_path.exists()
    content = hook_path.read_text(encoding="utf-8")
    assert "prepare-commit-msg" in content
    assert "winkers commit-fmt" in content


def test_cli_hooks_install(tmp_path):
    from click.testing import CliRunner

    from winkers.cli.main import cli

    result = CliRunner().invoke(cli, ["hooks", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Hook installed" in result.output
    assert (tmp_path / ".githooks" / "prepare-commit-msg").exists()
    assert load_commit_format(tmp_path) != {}


def test_fallback_message_no_git(tmp_path):
    """Fallback message when no git repo — returns generic."""
    msg = _fallback_message(tmp_path)
    assert msg.startswith("wip:")


def test_session_hook_uses_autocommit(tmp_path):
    """init installs autocommit hook instead of raw git commit."""
    import shutil
    from pathlib import Path

    from click.testing import CliRunner

    from winkers.cli.main import cli

    fixture = Path(__file__).parent / "fixtures" / "python_project"
    shutil.copytree(fixture, tmp_path / "project")
    project = tmp_path / "project"
    (project / ".claude").mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    import json
    settings = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    hooks = settings.get("hooks", {}).get("SessionEnd", [])
    commands = [
        h.get("command", "")
        for entry in hooks
        for h in entry.get("hooks", [])
    ]
    # Should use autocommit, not raw git commit -m 'wip: ...'
    assert any("autocommit" in cmd for cmd in commands)
    assert not any("wip: auto-commit" in cmd for cmd in commands)
