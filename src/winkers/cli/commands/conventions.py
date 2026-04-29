"""winkers conventions-migrate / cleanup-legacy."""

from __future__ import annotations

from pathlib import Path

import click


@click.command("conventions-migrate")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Accept all entries without interactive review.")
def conventions_migrate(path: str, yes: bool):
    """Migrate conventions/constraints from old semantic.json to rules.json.

    For projects that ran  winkers init  before v0.7.0, semantic.json may
    contain  conventions[]  and  constraints[]  fields.  This command reads
    them and imports them as rules with source 'migrated-from-semantic'.

    Safe to run multiple times — already-imported entries are skipped.
    """
    import json as _json
    from datetime import date

    from winkers.conventions import (
        ConventionRule,
        RulesStore,
        compile_overview,
    )
    from winkers.store import STORE_DIR

    root = Path(path).resolve()
    semantic_path = root / STORE_DIR / "semantic.json"

    if not semantic_path.exists():
        click.echo("No semantic.json found. Nothing to migrate.")
        return

    raw = _json.loads(semantic_path.read_text(encoding="utf-8"))
    entries: list[str] = []
    for field in ("conventions", "constraints"):
        val = raw.get(field)
        if isinstance(val, list):
            for v in val:
                if isinstance(v, dict):
                    text = v.get("content") or v.get("text") or v.get("rule") or ""
                    if text:
                        entries.append(str(text))
                elif v:
                    entries.append(str(v))

    if not entries:
        click.echo(
            "semantic.json has no 'conventions' or 'constraints' fields. Nothing to migrate."
        )
        return

    click.echo(f"Found {len(entries)} entries in semantic.json to migrate.\n")

    rules_store = RulesStore(root)
    rules_file = rules_store.load()

    # Skip entries that are already in rules.json (same content)
    existing_contents = {r.content for r in rules_file.rules}
    new_entries = [e for e in entries if e not in existing_contents]
    skipped_existing = len(entries) - len(new_entries)
    if skipped_existing:
        click.echo(f"  {skipped_existing} already imported — skipped.\n")

    if not new_entries:
        click.echo("All entries already in rules.json.")
        return

    today = date.today().isoformat()
    accepted = 0

    for idx, content in enumerate(new_entries, 1):
        click.echo(f"[{idx}/{len(new_entries)}] {content}")
        if yes:
            do_accept = True
        else:
            choice = click.prompt("  Accept? [y/n]", default="y")
            do_accept = choice.lower().startswith("y")

        if do_accept:
            rule = ConventionRule(
                id=rules_store.next_id(rules_file),
                category="architecture",
                title=content[:60].rstrip(),
                content=content,
                source="migrated-from-semantic",
                created=today,
            )
            rules_file.rules.append(rule)
            accepted += 1
        else:
            click.echo("  Skipped.")

    if accepted:
        rules_store.save(rules_file)
        compile_overview(rules_file, rules_store.overview_path)
        click.echo(f"\n[ok] Migrated {accepted} rule(s) to .winkers/rules/rules.json")
        click.echo("     overview.md updated.")
    else:
        click.echo("\nNo rules accepted.")


@click.command("cleanup-legacy")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Print what would be removed without deleting.",
)
def cleanup_legacy(path: str, dry_run: bool):
    """Remove legacy artifacts whose state was migrated to project.json/units.json.

    After Wave 4 of the 0.9.0 redesign these files are no longer the
    source of truth — they sit on disk as rollback safety nets:

    \b
      .winkers/semantic.json   -> .winkers/project.json::semantic
      .winkers/rules/rules.json -> .winkers/project.json::rules
      .winkers/impact.json     -> .winkers/units.json (per-unit)

    The cleanup is idempotent and skips files whose data hasn't been
    migrated yet (that would lose state). Run after a successful
    `winkers init` to reclaim disk space and ensure no consumer
    accidentally reads the stale legacy copy.

    `--dry-run` prints the plan without touching anything.
    """
    from winkers.descriptions.store import UnitsStore
    from winkers.project import PROJECT_FILE, ProjectStore
    from winkers.store import STORE_DIR

    root = Path(path).resolve()
    store_dir = root / STORE_DIR

    legacy_files: list[tuple[str, Path, str]] = []

    # semantic.json — migrated when project.json exists with non-default
    # semantic section. Conservative: require project.json present.
    sem = store_dir / "semantic.json"
    if sem.exists() and (store_dir / PROJECT_FILE).exists():
        bundle = ProjectStore(root).load()
        if bundle is not None:
            legacy_files.append((
                "semantic.json", sem,
                "folded into project.json (semantic section)",
            ))

    # rules/rules.json — same migration gate.
    rules = store_dir / "rules" / "rules.json"
    if rules.exists() and (store_dir / PROJECT_FILE).exists():
        legacy_files.append((
            "rules/rules.json", rules,
            "folded into project.json (rules section)",
        ))

    # impact.json — migrated when units.json carries function_unit
    # entries with `risk_level` (set by ImpactStore.save shim).
    imp = store_dir / "impact.json"
    if imp.exists():
        units = UnitsStore(root).load()
        has_impact = any(
            u.get("kind") == "function_unit" and u.get("risk_level")
            for u in units
        )
        if has_impact:
            legacy_files.append((
                "impact.json", imp,
                "folded into units.json (per-function risk fields)",
            ))

    if not legacy_files:
        click.echo("No legacy artifacts to clean up.")
        return

    total_bytes = sum(p.stat().st_size for _, p, _ in legacy_files)
    click.echo(f"Found {len(legacy_files)} legacy file(s), {total_bytes:,} bytes total:")
    for name, path_, reason in legacy_files:
        size = path_.stat().st_size
        click.echo(f"  {name}  ({size:,} bytes) — {reason}")

    if dry_run:
        click.echo("\n--dry-run: nothing removed.")
        return

    removed = 0
    for _, path_, _ in legacy_files:
        try:
            path_.unlink()
            removed += 1
        except OSError as e:
            click.echo(f"  WARN: cannot remove {path_}: {e}", err=True)

    click.echo(f"\n[ok] Removed {removed} legacy file(s).")
