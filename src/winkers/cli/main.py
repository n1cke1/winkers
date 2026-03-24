"""Winkers CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from winkers.graph import GraphBuilder
from winkers.resolver import CrossFileResolver
from winkers.store import GraphStore


@click.group()
@click.version_option(version=__import__("winkers").__version__)
def cli():
    """Winkers — architectural context layer for AI coding agents.

    \b
    Quick start:
      1. Set API key:  set ANTHROPIC_API_KEY=sk-ant-...
         (or create .env file in project root)
      2. winkers init          Build graph + semantic
      3. winkers serve         Start MCP server for AI agents
      4. winkers dashboard     Open browser graph
    """


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--no-semantic", is_flag=True, default=False,
              help="Skip semantic enrichment (no Claude API call).")
def init(path: str, no_semantic: bool):
    """Build the dependency graph for the project.

    Automatically detects your IDE and registers the MCP server:

    \b
      .claude/ or CLAUDE.md found  ->  Claude Code config
      .cursor/ found               ->  Cursor rules

    Semantic enrichment requires ANTHROPIC_API_KEY. Set it via:

    \b
      export ANTHROPIC_API_KEY=sk-ant-...   (Linux/Mac)
      set ANTHROPIC_API_KEY=sk-ant-...      (Windows cmd)
      $env:ANTHROPIC_API_KEY="sk-ant-..."   (PowerShell)

    Or create a .env file in the project root with ANTHROPIC_API_KEY=sk-ant-...

    If the key is not set, init still works — semantic is skipped.
    Use --no-semantic to skip explicitly.

    \b
    Corporate SSL proxy? Two options:
      1. pip install pip-system-certs   (recommended, one-time fix)
      2. set WINKERS_SSL_VERIFY=0       (quick workaround, less secure)
    """
    root = Path(path).resolve()
    click.echo(f"Scanning {root} ...")

    builder = GraphBuilder()
    graph = builder.build(root)

    click.echo("Resolving cross-file calls ...")
    CrossFileResolver().resolve(graph, str(root))

    store = GraphStore(root)
    store.save(graph)

    _update_gitignore(root)

    click.echo(
        f"Done. {len(graph.files)} files, {len(graph.functions)} functions, "
        f"{len(graph.call_edges)} call edges. ({graph.meta.get('parse_time_ms', 0):.0f} ms)"
    )

    _run_debt_analysis(root, graph)

    if not no_semantic:
        _run_semantic_enrichment(root, graph)

    _autodetect_ide(root)


def _load_dotenv(root: Path) -> None:
    """Load .env file from project root into os.environ."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _run_semantic_enrichment(root: Path, graph) -> None:
    """One Claude API call — generate architectural context for the project."""
    _load_dotenv(root)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        click.echo(
            "  Skipping semantic: ANTHROPIC_API_KEY not set.\n"
            "  Set it via: set ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Or create a .env file in the project root."
        )
        return
    click.echo(f"  API key found: {api_key[:12]}...")

    try:
        from winkers.semantic import SemanticEnricher, SemanticStore
    except ImportError:
        click.echo(
            "  Skipping semantic: 'anthropic' not installed. "
            "Run: pip install anthropic"
        )
        return

    sem_store = SemanticStore(root)
    existing = sem_store.load()

    try:
        enricher = SemanticEnricher()
    except Exception as e:
        click.echo(f"  Skipping semantic: {e}")
        return

    # Check if code changed since last enrichment
    if existing and not enricher.is_stale(graph, root, existing):
        click.echo("  Semantic data up to date, skipping API call.")
        return

    click.echo("  Generating semantic layer via Claude API ...")

    try:
        result = enricher.enrich(graph, root)
    except RuntimeError as e:
        click.echo(f"  Semantic enrichment failed: {e}")
        return

    sem_store.save(result)
    tokens = result.meta.get("input_tokens", 0) + result.meta.get("output_tokens", 0)
    secs = result.meta.get("duration_s", 0)
    click.echo(
        f"  [ok] Semantic: {len(result.zone_intents)} zones, "
        f"{len(result.constraints)} constraints, "
        f"{len(result.conventions)} conventions "
        f"({tokens} tokens, {secs}s)"
    )



def _run_debt_analysis(root: Path, graph) -> None:
    """Compute tech debt metrics and save to .winkers/debt.json."""
    from winkers.debt import compute_debt

    report = compute_debt(graph)
    debt_path = root / ".winkers" / "debt.json"
    debt_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    s = report.summary
    total = s.get("total_issues", 0)
    high = s.get("by_severity", {}).get("high", 0)
    medium = s.get("by_severity", {}).get("medium", 0)

    if total == 0:
        click.echo("  [ok] Tech debt: clean")
    else:
        click.echo(
            f"  Tech debt: {total} issues "
            f"({high} high, {medium} medium) -> .winkers/debt.json"
        )


def _autodetect_ide(root: Path) -> None:
    """Detect IDE from project files and auto-register MCP server."""
    detected = False

    # Claude Code: .claude/ directory or CLAUDE.md
    if (root / ".claude").is_dir() or (root / "CLAUDE.md").exists():
        _install_claude_code(root)
        detected = True

    # Cursor: .cursor/ directory
    if (root / ".cursor").is_dir():
        _install_cursor(root)
        detected = True

    if not detected:
        click.echo(
            "  No IDE detected. To register manually:\n"
            "    winkers init  (with .claude/ or .cursor/ present)"
        )


def _update_gitignore(root: Path) -> None:
    """Add .winkers/ to project .gitignore if not already present."""
    gitignore = root / ".gitignore"
    entry = ".winkers/"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if entry in content:
            return
        gitignore.write_text(content.rstrip() + f"\n{entry}\n", encoding="utf-8")
    else:
        gitignore.write_text(f"{entry}\n", encoding="utf-8")
    click.echo(f"  [ok] Added {entry} to .gitignore")


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "templates"


def _install_claude_code(root: Path) -> None:
    templates = _templates_dir() / "claude_code"

    # Skill
    skill_dst = root / ".claude" / "skills" / "winkers"
    skill_dst.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy(templates / "skill" / "SKILL.md", skill_dst / "SKILL.md")
    click.echo(f"  [ok] Skill installed: {skill_dst / 'SKILL.md'}")

    # Subagent
    agents_dst = root / ".claude" / "agents"
    agents_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(templates / "subagent.yaml", agents_dst / "winkers-advisor.yaml")
    click.echo(f"  [ok] Subagent installed: {agents_dst / 'winkers-advisor.yaml'}")

    # CLAUDE.md snippet
    snippet = (templates / "claude_md_snippet.md").read_text(encoding="utf-8")
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if "Winkers" not in existing:
            claude_md.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
            click.echo(f"  [ok] Appended Winkers snippet to {claude_md}")
        else:
            click.echo("  ~ CLAUDE.md already mentions Winkers, skipped.")
    else:
        claude_md.write_text(snippet, encoding="utf-8")
        click.echo(f"  [ok] Created {claude_md}")

    # MCP settings — user scope (~/.claude.json)
    claude_json = Path.home() / ".claude.json"
    settings: dict = {}
    if claude_json.exists():
        import json as _json
        try:
            settings = _json.loads(claude_json.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    # Use full path to winkers executable so Claude Code can find it
    import shutil as _shutil
    winkers_bin = _shutil.which("winkers") or "winkers"
    settings.setdefault("mcpServers", {})["winkers"] = {
        "command": winkers_bin,
        "args": ["serve", str(root)],
    }
    claude_json.write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )
    click.echo(f"  [ok] MCP server registered (user scope): {claude_json}")


def _install_cursor(root: Path) -> None:
    import shutil
    templates = _templates_dir() / "cursor"
    rules_dst = root / ".cursor" / "rules"
    rules_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(templates / "winkers.mdc", rules_dst / "winkers.mdc")
    click.echo(f"  [ok] Cursor rules installed: {rules_dst / 'winkers.mdc'}")


def _install_generic(root: Path) -> None:
    templates = _templates_dir() / "generic"
    snippet = (templates / "AGENTS.md").read_text(encoding="utf-8")
    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        existing = agents_md.read_text(encoding="utf-8")
        if "Winkers" not in existing:
            agents_md.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
            click.echo(f"  [ok] Appended Winkers snippet to {agents_md}")
        else:
            click.echo("  ~ AGENTS.md already mentions Winkers, skipped.")
    else:
        agents_md.write_text(snippet, encoding="utf-8")
        click.echo(f"  [ok] Created {agents_md}")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def serve(path: str):
    """Start the MCP server (stdio). AI agents connect here."""
    from winkers.mcp.server import run
    root = Path(path).resolve()
    run(root)


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--port", default=7420, show_default=True, help="HTTP port")
@click.option("--no-browser", is_flag=True, default=False, help="Don't open browser")
def dashboard(path: str, port: int, no_browser: bool):
    """Open the browser dependency graph."""
    import webbrowser

    from winkers.dashboard.api import run as run_dashboard

    root = Path(path).resolve()
    url = f"http://127.0.0.1:{port}"
    click.echo(f"Dashboard at {url}")
    if not no_browser:
        webbrowser.open(url)
    run_dashboard(root, port=port)


