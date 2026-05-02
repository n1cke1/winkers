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
    # `command` is the absolute path to the winkers binary (or bare "winkers"
    # fallback when no venv/PATH match). Set to absolute since 600cfb9 so
    # subprocess contexts with stripped PATH (systemd, ticket runners) keep
    # working — `uvx winkers serve` is no longer the default.
    cmd = mcp_data["mcpServers"]["winkers"]["command"]
    assert cmd.endswith("winkers") or cmd.endswith("winkers.exe") or cmd == "winkers"
    args = mcp_data["mcpServers"]["winkers"]["args"]
    assert args[0] == "serve"
    # Second arg is the absolute project path (not ".")
    assert str(project).replace("\\", "/") in args[1]
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


# ---------------------------------------------------------------------------
# _winkers_bin priority chain — CI regression guard
# ---------------------------------------------------------------------------


def test_winkers_bin_ignores_pytest_argv0(monkeypatch, tmp_path):
    """sys.argv[0] under pytest points at .../bin/pytest; the resolver
    must NOT treat it as the winkers binary just because it's an
    absolute path that exists. Prior bug: CI run wrote `command:
    "/opt/hostedtoolcache/.../bin/pytest"` into .mcp.json, breaking
    `test_init_autodetects_claude_code` on every push since 1e132db.
    """
    from winkers.cli.init_pipeline import bootstrap

    # Create a fake exe-dir layout mimicking the GitHub-runner
    # hostedtoolcache (bin/python3 + bin/winkers siblings).
    exe_dir = tmp_path / "bin"
    exe_dir.mkdir()
    fake_python = exe_dir / "python3"
    fake_python.write_text("")
    fake_winkers = exe_dir / "winkers"
    fake_winkers.write_text("")
    fake_pytest = exe_dir / "pytest"
    fake_pytest.write_text("")

    # No active venv, sys.argv[0]=pytest, sys.executable=python3 sibling.
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap.sys, "argv", [str(fake_pytest)])
    monkeypatch.setattr(bootstrap.sys, "executable", str(fake_python))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: None)

    # Must skip the pytest argv[0] and pick up the sibling winkers
    # script. (We don't assert "pytest not in result" — pytest's tmp_path
    # convention is `/tmp/pytest-of-<user>/...`, so "pytest" appears in
    # the parent path even on a correctly-resolved winkers binary.)
    assert bootstrap._winkers_bin() == str(fake_winkers)


def test_winkers_bin_uses_argv0_when_basename_matches(monkeypatch, tmp_path):
    """When sys.argv[0] really IS the winkers entry script, prefer it
    (preserves the original priority — pipx etc.)."""
    from winkers.cli.init_pipeline import bootstrap

    exe_dir = tmp_path / "bin"
    exe_dir.mkdir()
    fake_winkers = exe_dir / "winkers"
    fake_winkers.write_text("")

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(bootstrap.sys, "argv", [str(fake_winkers)])
    # sys.executable points elsewhere — argv[0] should still win.
    other = tmp_path / "elsewhere" / "python3"
    other.parent.mkdir()
    other.write_text("")
    monkeypatch.setattr(bootstrap.sys, "executable", str(other))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: None)

    assert bootstrap._winkers_bin() == str(fake_winkers)


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
