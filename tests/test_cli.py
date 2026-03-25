"""Tests for CLI commands."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from winkers.cli.main import cli

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Copy python fixture to tmp_path so CLI can write .winkers/ there."""
    shutil.copytree(PYTHON_FIXTURE, tmp_path / "project")
    return tmp_path / "project"


def test_init_exit_code(project: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0, result.output


def test_init_creates_graph_json(project: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    assert (project / ".winkers" / "graph.json").exists()


def test_init_output_contains_files(project: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert "files" in result.output or "functions" in result.output


def test_help_shows_commands():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "serve" in result.output
    assert "dashboard" in result.output


def test_init_autodetects_claude_code(project: Path):
    """If .claude/ exists, init auto-registers MCP config in user scope."""
    (project / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0, result.output
    # MCP config goes to ~/.claude.json (user scope only)
    claude_json = Path.home() / ".claude.json"
    assert claude_json.exists()
    # Project-level settings.json has SessionEnd hook
    proj_settings = project / ".claude" / "settings.json"
    assert proj_settings.exists()
    import json as _json
    data = _json.loads(proj_settings.read_text(encoding="utf-8"))
    assert "SessionEnd" in data.get("hooks", {})


def test_init_autodetects_cursor(project: Path):
    """If .cursor/ exists, init auto-installs cursor rules."""
    (project / ".cursor").mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0, result.output
    assert (project / ".cursor" / "rules" / "winkers.mdc").exists()


def test_init_no_ide_detected(project: Path):
    """If no IDE markers, init still works and shows message."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0, result.output
    assert "No IDE detected" in result.output
