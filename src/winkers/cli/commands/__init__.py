"""Winkers CLI commands — one module per command (or close-related group)."""

from __future__ import annotations

import click

from winkers.cli.commands.analyze import analyze
from winkers.cli.commands.dashboard import dashboard
from winkers.cli.commands.doctor import doctor
from winkers.cli.commands.git_cmds import autocommit, commit_fmt, commits_normalize, hooks_install
from winkers.cli.commands.improve import improve
from winkers.cli.commands.init import init
from winkers.cli.commands.protect import protect
from winkers.cli.commands.record import record
from winkers.cli.commands.serve import serve


def register_commands(cli: click.Group) -> None:
    """Attach every command to the root `cli` group."""
    cli.add_command(init)
    cli.add_command(record)
    cli.add_command(analyze)
    cli.add_command(improve)
    cli.add_command(doctor)
    cli.add_command(protect)
    cli.add_command(hooks_install)
    cli.add_command(commit_fmt)
    cli.add_command(autocommit)
    cli.add_command(commits_normalize)
    cli.add_command(serve)
    cli.add_command(dashboard)
