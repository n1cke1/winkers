"""Tests for commit format module."""

from winkers.commit_format import (
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
