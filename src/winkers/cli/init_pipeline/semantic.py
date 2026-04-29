"""Semantic enrichment + interactive rules audit for `winkers init`."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from winkers.cli.init_pipeline.bootstrap import _backup_file, _load_dotenv

if TYPE_CHECKING:
    from winkers.conventions import RulesAudit, RulesFile, RulesStore


def _run_semantic_enrichment(root: Path, graph, yes: bool = False, force: bool = False) -> None:
    """One Claude API call -- generate architectural context and audit rules."""
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

    from winkers.conventions import (
        DismissedStore,
        RulesStore,
        compile_overview,
    )
    from winkers.detectors import run_all_detectors

    rules_store = RulesStore(root)
    dismissed_store = DismissedStore(root)
    rules_file = rules_store.load()
    dismissed = dismissed_store.load()

    sem_store = SemanticStore(root)
    existing = sem_store.load()

    try:
        enricher = SemanticEnricher()
    except Exception as e:
        click.echo(f"  Skipping semantic: {e}")
        return

    if existing and not enricher.is_stale(graph, root, existing) and not force:
        click.echo("  Semantic data up to date, skipping API call.")
        return

    click.echo("  Running pattern detectors ...")
    evidence = run_all_detectors(root)
    if evidence:
        click.echo(f"  Found {len(evidence)} detector pattern(s).")

    click.echo("  Generating semantic layer via Claude API ...")

    try:
        result = enricher.enrich(
            graph, root,
            existing_rules=rules_file.rules,
            detector_evidence=evidence,
            dismissed=dismissed,
        )
    except RuntimeError as e:
        click.echo(f"  Semantic enrichment failed: {e}")
        return

    # Preserve user-defined constraints — never overwritten by AI
    if existing:
        result.layer.constraints = existing.constraints
    _backup_file(sem_store.semantic_path, root / ".winkers" / "history", "semantic")
    sem_store.save(result.layer)
    tokens = result.layer.meta.get("input_tokens", 0) + result.layer.meta.get("output_tokens", 0)
    secs = result.layer.meta.get("duration_s", 0)
    click.echo(
        f"  [ok] Semantic: {len(result.layer.zone_intents)} zones "
        f"({tokens} tokens, {secs}s)"
    )

    audit = result.rules_audit
    if audit.is_empty():
        return

    filtered_audit, dis_adds, dis_removes, dis_updates = _interactive_review(audit, rules_file, yes)

    if dis_adds or dis_removes or dis_updates:
        dismissed_store.merge(dis_adds, dis_removes, dis_updates)

    if not filtered_audit.is_empty():
        added, updated, removed = _apply_audit(rules_file, filtered_audit, rules_store)
        _backup_file(rules_store.rules_path, root / ".winkers" / "history", "rules")
        rules_store.save(rules_file)
        compile_overview(rules_file, rules_store.overview_path)
        click.echo(f"  [ok] Rules: +{added} added, {updated} updated, {removed} removed")


def _interactive_review(
    audit: RulesAudit, rules_file: RulesFile, yes: bool
) -> tuple[RulesAudit, list, list[int], list[int]]:
    """Review proposed rule changes one by one.

    Y = accept, n = skip (dismissed), q = accept this and all remaining.
    Returns (filtered_audit, dismissed_adds, dismissed_remove_ids, dismissed_update_ids).
    """
    from winkers.conventions import RulesAudit

    if yes or not sys.stdout.isatty():
        return audit, [], [], []

    rules_by_id = {r.id: r for r in rules_file.rules}
    dismissed_adds = []
    dismissed_removes: list[int] = []
    dismissed_updates: list[int] = []
    selected_add = []
    selected_update = []
    selected_remove = []

    total = len(audit.add) + len(audit.update) + len(audit.remove)
    click.echo(f"\n  {total} rule change(s) proposed. Review each  (q = accept rest):")

    quit_all = False

    def _ask(prompt_text: str) -> str:
        return click.prompt(f"  {prompt_text}", default="y", show_default=False).strip().lower()

    def _trunc(s: str, n: int = 200) -> str:
        return s if len(s) <= n else s[:n] + "…"

    for i, r in enumerate(audit.add, 1):
        if quit_all:
            selected_add.append(r)
            continue
        click.echo(f"\n  [{i}/{total}] ADD  [{r.category}]  {r.title}")
        click.echo(f"  content:  {_trunc(r.content)}")
        if r.wrong_approach:
            click.echo(f"  avoid:    {_trunc(r.wrong_approach)}")
        if r.affects:
            click.echo(f"  affects:  {', '.join(r.affects)}")
        choice = _ask("Accept? [Y/n/q]")
        if choice.startswith("q"):
            quit_all = True
            selected_add.append(r)
        elif choice.startswith("n"):
            dismissed_adds.append(r)
        else:
            selected_add.append(r)

    for i, r in enumerate(audit.update, len(audit.add) + 1):
        if quit_all:
            selected_update.append(r)
            continue
        current = rules_by_id.get(r.id)
        click.echo(f"\n  [{i}/{total}] UPDATE  rule #{r.id}"
                   + (f"  [{current.title}]" if current else ""))
        if current and r.content and r.content != current.content:
            click.echo(f"  was:      {_trunc(current.content, 120)}")
            click.echo(f"  now:      {_trunc(r.content, 120)}")
        elif r.content:
            click.echo(f"  content:  {_trunc(r.content, 120)}")
        if current and r.wrong_approach and r.wrong_approach != current.wrong_approach:
            click.echo(f"  avoid→    {_trunc(r.wrong_approach, 120)}")
        if r.reason:
            click.echo(f"  reason:   {_trunc(r.reason, 120)}")
        choice = _ask("Accept? [Y/n/q]")
        if choice.startswith("q"):
            quit_all = True
            selected_update.append(r)
        elif choice.startswith("n"):
            dismissed_updates.append(r.id)
        else:
            selected_update.append(r)

    for i, r in enumerate(audit.remove, len(audit.add) + len(audit.update) + 1):
        if quit_all:
            selected_remove.append(r)
            continue
        current = rules_by_id.get(r.id)
        click.echo(f"\n  [{i}/{total}] REMOVE  rule #{r.id}")
        if current:
            click.echo(f"  title:    {current.title}")
            click.echo(f"  content:  {_trunc(current.content, 120)}")
        if r.reason:
            click.echo(f"  reason:   {_trunc(r.reason, 120)}")
        choice = _ask("Accept removal? [Y/n/q]")
        if choice.startswith("q"):
            quit_all = True
            selected_remove.append(r)
        elif choice.startswith("n"):
            dismissed_removes.append(r.id)
        else:
            selected_remove.append(r)

    if quit_all:
        click.echo("  Accepted all remaining.")

    filtered = RulesAudit(add=selected_add, update=selected_update, remove=selected_remove)
    return filtered, dismissed_adds, dismissed_removes, dismissed_updates


def _apply_audit(
    rules_file: RulesFile, audit: RulesAudit, store: RulesStore,
) -> tuple[int, int, int]:
    """Apply audit to rules_file in-place. Returns (added, updated, removed)."""
    from datetime import date

    from winkers.conventions import ConventionRule

    today = date.today().isoformat()
    added = 0
    for item in audit.add:
        rules_file.rules.append(ConventionRule(
            id=store.next_id(rules_file),
            category=item.category,
            title=item.title,
            content=item.content,
            wrong_approach=item.wrong_approach,
            affects=item.affects,
            related=item.related,
            source="semantic-agent",
            created=today,
        ))
        added += 1

    updated = 0
    for item in audit.update:
        for rule in rules_file.rules:
            if rule.id == item.id:
                if item.title:
                    rule.title = item.title
                if item.content:
                    rule.content = item.content
                if item.wrong_approach:
                    rule.wrong_approach = item.wrong_approach
                updated += 1
                break

    protected = {"manual", "migrated-from-semantic"}
    remove_ids = {
        item.id for item in audit.remove
        if not any(r.id == item.id and r.source in protected for r in rules_file.rules)
    }
    before = len(rules_file.rules)
    rules_file.rules = [r for r in rules_file.rules if r.id not in remove_ids]
    removed = before - len(rules_file.rules)

    return added, updated, removed
