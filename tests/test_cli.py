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
    """If .claude/ exists, init creates .mcp.json and session hooks."""
    (project / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0, result.output
    # MCP config goes to project-level .mcp.json (portable)
    mcp_json = project / ".mcp.json"
    assert mcp_json.exists()
    import json as _json
    mcp_data = _json.loads(mcp_json.read_text(encoding="utf-8"))
    assert mcp_data["mcpServers"]["winkers"]["command"] == "uvx"
    args = mcp_data["mcpServers"]["winkers"]["args"]
    assert args[0] == "winkers"
    assert args[1] == "serve"
    # Third arg is the absolute project path (not ".")
    assert str(project).replace("\\", "/") in args[2]
    # Project-level settings.json has SessionEnd hook
    proj_settings = project / ".claude" / "settings.json"
    assert proj_settings.exists()
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


def test_doctor_runs(project: Path):
    """doctor command runs and reports ok/warnings."""
    runner = CliRunner()
    # Init first to create graph
    runner.invoke(cli, ["init", str(project)])
    result = runner.invoke(cli, ["doctor", str(project)])
    assert result.exit_code == 0
    assert "ok" in result.output
    assert "Python" in result.output
    assert "graph.json" in result.output


def test_doctor_no_graph(project: Path):
    """doctor warns when no graph.json exists."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", str(project)])
    assert result.exit_code == 0
    assert "No graph.json" in result.output


def test_schema_version_in_graph(project: Path):
    """Graph meta includes schema_version after build."""
    from winkers.store import GraphStore
    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    graph = GraphStore(project).load()
    assert graph.meta.get("schema_version") == "2"


def test_winkers_pointer_fresh_install(project: Path):
    """_install_winkers_pointer appends a static pointer block (no semantic data)."""
    from winkers.cli.main import _install_winkers_pointer
    from winkers.semantic import SemanticLayer, SemanticStore

    claude_md = project / "CLAUDE.md"
    claude_md.write_text("# My project\n", encoding="utf-8")

    # semantic.json exists but pointer must NOT copy its content.
    SemanticStore(project).save(SemanticLayer(
        data_flow="CSV -> parser -> DB",
        domain_context="Carbon accounting for EU ETS.",
    ))
    _install_winkers_pointer(project)

    content = claude_md.read_text(encoding="utf-8")
    assert "<!-- winkers-start -->" in content
    assert "<!-- winkers-end -->" in content
    assert "orient(" in content
    # Semantic data from semantic.json must NOT leak into CLAUDE.md.
    assert "CSV -> parser -> DB" not in content
    assert "Carbon accounting" not in content


def test_winkers_pointer_idempotent(project: Path):
    """Two calls produce exactly one pointer block."""
    from winkers.cli.main import _install_winkers_pointer

    claude_md = project / "CLAUDE.md"
    claude_md.write_text("# My project\n", encoding="utf-8")

    _install_winkers_pointer(project)
    _install_winkers_pointer(project)

    content = claude_md.read_text(encoding="utf-8")
    assert content.count("<!-- winkers-start -->") == 1
    assert content.count("<!-- winkers-end -->") == 1


def test_winkers_pointer_migrates_old_semantic_block(project: Path):
    """Legacy `<!-- winkers-semantic-start -->` block is replaced in-place."""
    from winkers.cli.main import _install_winkers_pointer

    claude_md = project / "CLAUDE.md"
    claude_md.write_text(
        "# My project\n\n"
        "<!-- winkers-semantic-start -->\n"
        "### Project context (auto-generated)\n\n"
        "- **Data flow**: CSV -> parser -> DB\n"
        "- **Domain**: Carbon accounting for EU ETS.\n"
        "<!-- winkers-semantic-end -->\n",
        encoding="utf-8",
    )

    _install_winkers_pointer(project)

    content = claude_md.read_text(encoding="utf-8")
    # Old markers and content gone.
    assert "<!-- winkers-semantic-start -->" not in content
    assert "<!-- winkers-semantic-end -->" not in content
    assert "CSV -> parser -> DB" not in content
    # New pointer in place.
    assert "<!-- winkers-start -->" in content
    assert "orient(" in content
