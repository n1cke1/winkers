"""winkers couplings."""

from __future__ import annotations

from pathlib import Path

import click

# ---------------------------------------------------------------------------
# couplings — detect cross-file couplings from hardcoded_artifacts
# ---------------------------------------------------------------------------

@click.command("couplings")
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root containing .winkers/units.json")
@click.option("--save", is_flag=True, default=False,
              help="Append detected couplings to .winkers/units.json as "
                   "traceability_units (replaces existing auto-detected ones)")
@click.option("--min-files", type=int, default=2,
              help="Minimum distinct files for a cluster to qualify")
@click.option("--min-hits", type=int, default=2,
              help="Minimum total hits for a cluster")
@click.option("--limit", type=int, default=30,
              help="Maximum clusters to display")
def couplings(root: str, save: bool, min_files: int, min_hits: int,
              limit: int):
    """Detect cross-file couplings from hardcoded_artifacts.

    Reads .winkers/units.json (populated by `winkers describe-fn` /
    `describe-section`), inverts the artifact lists into a value→units
    map, and emits clusters where the same canonical value appears in
    multiple files. Each cluster becomes a proposed traceability_unit.

    \b
    Workflow:
      winkers describe-fn <fn_id> --save        # populate function units
      winkers describe-section <ref> --save     # populate template units
      winkers couplings                          # inspect couplings
      winkers couplings --save                   # commit to units.json
    """
    import json as _json

    from winkers.descriptions.aggregator import (
        detect_couplings,
        proposed_to_unit,
    )

    root_path = Path(root).resolve()
    units_path = root_path / ".winkers" / "units.json"
    if not units_path.exists():
        click.echo(
            f"No units file at {units_path}. Run `winkers describe-fn` / "
            f"`winkers describe-section --save` first.",
            err=True,
        )
        raise SystemExit(2)

    data = _json.loads(units_path.read_text(encoding="utf-8"))
    units = data.get("units", [])
    # Detection runs only on units with artifacts; auto-detected coupling
    # units (origin=auto-detected) are excluded so we don't bootstrap
    # couplings from prior couplings.
    candidate = [
        u for u in units
        if u.get("kind") in ("function_unit", "traceability_unit")
        and u.get("hardcoded_artifacts")
        and (u.get("meta") or {}).get("origin") != "auto-detected"
    ]

    click.echo(
        f"Scanning {len(candidate)} units with hardcoded_artifacts "
        f"(of {len(units)} total)...",
        err=True,
    )

    clusters = detect_couplings(
        candidate, min_hits=min_hits, min_files=min_files,
    )

    if not clusters:
        click.echo("No cross-file couplings detected.")
        return

    click.echo(f"\n{len(clusters)} coupling cluster(s) found:\n")
    for c in clusters[:limit]:
        display = c.canonical_value
        if len(display) > 50:
            display = display[:47] + "..."
        click.echo(
            f"  [{c.primary_kind}]  {display!r}  "
            f"— {c.file_count} files, {c.hit_count} hits, "
            f"uniformity={c.kind_uniformity:.2f}"
        )
        for h in c.hits[:4]:
            ctx = h.artifact.context[:60]
            click.echo(f"      • {h.file:35s}  {h.unit_id:60s}  {ctx}")
        if len(c.hits) > 4:
            click.echo(f"      • ... and {len(c.hits) - 4} more")
        click.echo()

    if len(clusters) > limit:
        click.echo(f"  ... {len(clusters) - limit} more cluster(s) "
                   f"omitted (use --limit to show more)")

    if save:
        # Strip prior auto-detected couplings; replace with current set.
        kept = [u for u in units
                if (u.get("meta") or {}).get("origin") != "auto-detected"]
        proposed_units = [proposed_to_unit(c) for c in clusters]
        new_data = {"units": kept + proposed_units}
        units_path.write_text(
            _json.dumps(new_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(
            f"\nSaved {len(proposed_units)} traceability_units to "
            f"{units_path} (replaced {len(units) - len(kept)} prior "
            f"auto-detected entries)",
            err=True,
        )
