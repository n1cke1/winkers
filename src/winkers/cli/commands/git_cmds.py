"""winkers hooks / commit-fmt / autocommit / commits."""

from __future__ import annotations

from pathlib import Path

import click

from winkers.cli.init_pipeline import (
    _load_dotenv,
)


@click.command("hooks")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--template", default="[{ticket}] {message}",
              help="Commit message template. Variables: {message}, {ticket}, {date}, {author}.")
@click.option("--ticket-pattern", default=r"[A-Z]+-\d+",
              help="Regex to extract ticket from branch or message.")
def hooks_install(path: str, template: str, ticket_pattern: str):
    """Install git hooks and configure commit format.

    \b
    Installs .githooks/prepare-commit-msg that applies the template.
    Also saves the format in .winkers/config.json.

    \b
    After install, run:
      git config core.hooksPath .githooks
    """
    root = Path(path).resolve()

    from winkers.commit_format import install_hook, save_commit_format

    save_commit_format(root, template, ticket_pattern)
    hook_path = install_hook(root)

    click.echo("  [ok] Commit format saved to .winkers/config.json")
    click.echo(f"  [ok] Hook installed: {hook_path.relative_to(root)}")
    click.echo("  Run: git config core.hooksPath .githooks")


@click.command("commit-fmt", hidden=True)
@click.argument("msg_file", type=click.Path(exists=True))
def commit_fmt(msg_file: str):
    """Format a commit message file (called by prepare-commit-msg hook)."""
    msg_path = Path(msg_file)
    root = Path(".").resolve()

    from winkers.commit_format import format_message, load_commit_format

    fmt = load_commit_format(root)
    if not fmt:
        return

    template = fmt.get("template", "{message}")
    ticket_pattern = fmt.get("ticket_pattern", r"[A-Z]+-\d+")

    original = msg_path.read_text(encoding="utf-8").strip()
    if not original:
        return

    formatted = format_message(original, template, ticket_pattern)
    msg_path.write_text(formatted + "\n", encoding="utf-8")


@click.command("autocommit")
@click.argument("path", default=".", type=click.Path(exists=True))
def autocommit(path: str):
    """Generate a commit message via Haiku and commit staged changes.

    \b
    Intended for the SessionEnd hook:
      winkers autocommit

    Generates a meaningful message from the staged diff via Claude API.
    Falls back to file/function list if API is unavailable.
    Applies the configured commit_format template if set.
    """
    import subprocess as _sp

    root = Path(path).resolve()
    _load_dotenv(root)

    # Check there are staged changes
    try:
        _sp.check_output(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(root), stderr=_sp.DEVNULL,
        )
        # exit code 0 = no staged changes
        return
    except _sp.CalledProcessError:
        pass  # exit code 1 = there are staged changes

    from winkers.commit_format import (
        format_message,
        generate_commit_message,
        load_commit_format,
    )

    msg = generate_commit_message(root)

    # Apply template if configured
    fmt = load_commit_format(root)
    if fmt and fmt.get("template"):
        msg = format_message(
            msg,
            fmt["template"],
            fmt.get("ticket_pattern", r"[A-Z]+-\d+"),
        )

    try:
        _sp.check_call(
            ["git", "commit", "-m", msg],
            cwd=str(root),
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
        click.echo(f"  [ok] {msg}")
    except _sp.CalledProcessError:
        click.echo("  [!!] git commit failed", err=True)


@click.command("commits")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--range", "git_range", default="HEAD~5..HEAD",
              help="Git range (default: HEAD~5..HEAD).")
@click.option("--enrich", is_flag=True, default=False,
              help="Use Haiku to generate better messages for all commits in range.")
@click.option("--dry-run/--apply", default=True,
              help="Show changes without applying (default: dry-run).")
def commits_normalize(path: str, git_range: str, enrich: bool, dry_run: bool):
    """Normalize or enrich commit messages.

    \b
    winkers commits --range HEAD~10..HEAD              Template normalization
    winkers commits --enrich --range HEAD~20..HEAD     AI-powered enrichment
    winkers commits --enrich --apply                   Rewrite with enriched messages
    """
    root = Path(path).resolve()

    if enrich:
        _commits_enrich(root, git_range, dry_run)
    else:
        _commits_template(root, git_range, dry_run)


def _commits_template(root: Path, git_range: str, dry_run: bool) -> None:
    """Normalize commits using the configured template."""
    from winkers.commit_format import load_commit_format, normalize_commits

    fmt = load_commit_format(root)
    if not fmt:
        click.echo("No commit_format in config. Run: winkers hooks")
        return

    results = normalize_commits(root, git_range, dry_run=dry_run)
    if not results:
        click.echo("No commits need normalization.")
        return

    for r in results:
        click.echo(f"  {r['hash']}  {r['old']}")
        click.echo(f"        -> {r['new']}")

    if dry_run:
        click.echo(
            f"\n{len(results)} commit(s) to normalize."
            " Run with --apply to rewrite."
        )


def _commits_enrich(root: Path, git_range: str, dry_run: bool) -> None:
    """Enrich commit messages using Haiku (diff + session context)."""
    import subprocess as _sp

    _load_dotenv(root)

    try:
        log_output = _sp.check_output(
            ["git", "log", "--format=%H|%s|%aI|%an", git_range],
            text=True, cwd=str(root), stderr=_sp.DEVNULL,
        ).strip()
    except Exception:
        click.echo("Could not read git log.")
        return

    if not log_output:
        click.echo("No commits in range.")
        return

    from winkers.commit_format import (
        enrich_commit,
        format_message,
        load_commit_format,
    )

    fmt = load_commit_format(root)
    template = fmt.get("template") if fmt else None
    ticket_pattern = fmt.get("ticket_pattern", r"[A-Z]+-\d+") if fmt else r"[A-Z]+-\d+"

    results = []
    for line in log_output.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commit_hash, old_msg, date, author = parts

        new_msg = enrich_commit(root, commit_hash)
        if new_msg is None:
            continue

        # Apply template if configured
        if template:
            new_msg = format_message(new_msg, template, ticket_pattern)

        if new_msg != old_msg:
            results.append({
                "hash": commit_hash[:8],
                "old": old_msg,
                "new": new_msg,
                "date": date[:10],
                "author": author,
            })

    if not results:
        click.echo("No commits to enrich.")
        return

    for r in results:
        click.echo(f"  {r['hash']}  {r['date']}  {r['author']}")
        click.echo(f"    old: {r['old']}")
        click.echo(f"    new: {r['new']}")

    if dry_run:
        click.echo(
            f"\n{len(results)} commit(s) to enrich."
            " Run with --apply to rewrite."
        )
    else:
        click.echo(
            f"\n{len(results)} commit(s) enriched."
            " Use git rebase -i to apply the new messages."
        )
