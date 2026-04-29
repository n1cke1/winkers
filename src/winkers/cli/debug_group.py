"""`winkers debug ...` — development / prompt-tuning utilities.

These commands aren't part of the day-to-day workflow; they exist so
that prompt changes and provider config can be exercised on a single
unit at a time. The full pipeline runs via `winkers init --with-units`
(incremental, hash-aware) — only reach for these when you want to
re-author / inspect one specific function, template section, data
file, or to evaluate the intent provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from winkers.store import GraphStore


@click.group()
def debug():
    """Development utilities — single-unit re-author, intent eval."""


@debug.command("describe-fn")
@click.argument("fn_id")
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root containing .winkers/graph.json")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the formatted prompt without invoking claude")
@click.option("--save", is_flag=True, default=False,
              help="Append/replace this unit in .winkers/units.json")
def describe_fn(fn_id: str, root: str, dry_run: bool, save: bool):
    """Generate a rich description for one function unit.

    Reads .winkers/graph.json to find the function and its 1-2 nearest
    callers, formats the description-author prompt, and either prints
    the prompt (--dry-run) or invokes `claude --print` to produce the
    description (subscription-first, no API key required).

    \b
    Example:
      winkers debug describe-fn engine/chp_model.py::solve_design -r /path/to/project
    """
    import json as _json

    from winkers.descriptions.author import author_function_description
    from winkers.descriptions.prompts import format_function_prompt

    root_path = Path(root).resolve()
    graph_path = root_path / ".winkers" / "graph.json"
    if not graph_path.exists():
        click.echo(
            f"No graph at {graph_path}. Run `winkers init` first.",
            err=True,
        )
        raise SystemExit(2)

    graph = _json.loads(graph_path.read_text(encoding="utf-8"))
    fn = graph["functions"].get(fn_id)
    if fn is None:
        click.echo(f"Function not found: {fn_id}", err=True)
        # Help the user discover correct ids.
        suffix = fn_id.split("::")[-1] if "::" in fn_id else fn_id
        candidates = [k for k in graph["functions"] if suffix in k]
        if candidates:
            click.echo("Similar fn_ids:", err=True)
            for c in candidates[:5]:
                click.echo(f"  {c}", err=True)
        raise SystemExit(2)

    # Source slice from line range — graph.json holds the boundaries already.
    src_path = root_path / fn["file"]
    if not src_path.exists():
        click.echo(f"Source file missing: {src_path}", err=True)
        raise SystemExit(2)
    src_lines = src_path.read_text(encoding="utf-8").splitlines()
    fn_source = "\n".join(src_lines[fn["line_start"] - 1: fn["line_end"]])

    # Top 2 callers, signatures only — see prompts.py docstring for why
    # bodies are deliberately excluded (cache-invalidation scope).
    caller_ids: list[str] = []
    for edge in graph.get("call_edges", []):
        if edge["target_fn"] == fn_id and edge["source_fn"] not in caller_ids:
            caller_ids.append(edge["source_fn"])
            if len(caller_ids) >= 2:
                break
    caller_sigs: list[str] = []
    for cid in caller_ids:
        c = graph["functions"].get(cid)
        if not c:
            continue
        params = ", ".join(p["name"] for p in c.get("params", []))
        prefix = f"{c['class_name']}." if c.get("class_name") else ""
        caller_sigs.append(f"def {prefix}{c['name']}({params})")

    # Display name in prompt — qualified for methods.
    display_name = (
        f"{fn['class_name']}.{fn['name']}"
        if fn.get("class_name") else fn["name"]
    )

    if dry_run:
        prompt = format_function_prompt(
            fn_source, fn["file"], display_name, callers=caller_sigs,
        )
        click.echo(prompt)
        return

    click.echo(f"Generating description for {fn_id}...", err=True)
    desc = author_function_description(
        fn_source=fn_source,
        file_path=fn["file"],
        fn_name=display_name,
        callers=caller_sigs,
        cwd=root_path,
    )
    if desc is None:
        click.echo("Description generation failed — see logs above.", err=True)
        raise SystemExit(1)

    unit = {
        "id": fn_id,
        "kind": "function_unit",
        "name": display_name,
        "anchor": {
            "file": fn["file"],
            "fn": fn["name"],
            **({"class": fn["class_name"]} if fn.get("class_name") else {}),
        },
        "source_hash": fn.get("ast_hash"),
        "description": desc.description,
        "hardcoded_artifacts": [a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts],
    }
    click.echo(_json.dumps(unit, ensure_ascii=False, indent=2))

    if save:
        # Lightweight merge until units_store.py (Phase 1.7) lands.
        units_path = root_path / ".winkers" / "units.json"
        existing = {"units": []}
        if units_path.exists():
            try:
                existing = _json.loads(units_path.read_text(encoding="utf-8"))
                existing.setdefault("units", [])
            except Exception:
                pass
        existing["units"] = [u for u in existing["units"]
                             if u.get("id") != fn_id]
        existing["units"].append(unit)
        units_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"Saved to {units_path}", err=True)


@debug.command("describe-section")
@click.argument("section_ref")
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print formatted prompt without invoking claude")
@click.option("--save", is_flag=True, default=False,
              help="Append/replace this unit in .winkers/units.json")
def describe_section(section_ref: str, root: str, dry_run: bool, save: bool):
    """Generate a description for one template section.

    SECTION_REF format: "<template-path>#<section-id>"

    \b
    Example:
      winkers debug describe-section templates/index.html#calc-sub-approach \\
                                     --root /path/to/project
    """
    import json as _json

    from winkers.descriptions.author import author_template_description
    from winkers.descriptions.prompts import format_template_section_prompt
    from winkers.templates.scanner import filter_leaves, scan_template

    if "#" not in section_ref:
        click.echo(
            "Section ref must be '<template-path>#<section-id>' "
            "(e.g. 'templates/index.html#calc-sub-approach')",
            err=True,
        )
        raise SystemExit(2)

    template_rel, section_id = section_ref.split("#", 1)
    root_path = Path(root).resolve()
    template_path = root_path / template_rel
    if not template_path.exists():
        click.echo(f"Template not found: {template_path}", err=True)
        raise SystemExit(2)

    sections = scan_template(template_path)
    sec = next((s for s in sections if s.id == section_id), None)
    if sec is None:
        click.echo(f"Section #{section_id} not found in {template_rel}", err=True)
        ids = sorted(s.id for s in filter_leaves(sections))
        if ids:
            click.echo("Available leaf section ids:", err=True)
            for i in ids:
                click.echo(f"  {i}", err=True)
        raise SystemExit(2)

    # Neighbor ids — gives the LLM orientation; keeps the prompt cheap by
    # not including their content.
    neighbors = [s.id for s in filter_leaves(sections) if s.id != section_id][:5]

    if dry_run:
        prompt = format_template_section_prompt(
            section_html=sec.content,
            file_path=template_rel,
            section_id=section_id,
            leading_comment=sec.leading_comment,
            neighbor_section_ids=neighbors,
        )
        click.echo(prompt)
        return

    click.echo(f"Generating description for {section_ref}...", err=True)
    desc = author_template_description(
        section_html=sec.content,
        file_path=template_rel,
        section_id=section_id,
        leading_comment=sec.leading_comment,
        neighbor_section_ids=neighbors,
        cwd=root_path,
    )
    if desc is None:
        click.echo("Description generation failed — see logs above.", err=True)
        raise SystemExit(1)

    unit_id = f"template:{template_rel}#{section_id}"
    unit = {
        "id": unit_id,
        "kind": "traceability_unit",
        "name": f"Section #{section_id} ({template_rel})",
        "source_files": [template_rel],
        "source_anchors": [f"{template_rel}#{section_id}"],
        "description": desc.description,
        "hardcoded_artifacts": [a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts],
    }
    click.echo(_json.dumps(unit, ensure_ascii=False, indent=2))

    if save:
        units_path = root_path / ".winkers" / "units.json"
        existing = {"units": []}
        if units_path.exists():
            try:
                existing = _json.loads(units_path.read_text(encoding="utf-8"))
                existing.setdefault("units", [])
            except Exception:
                pass
        existing["units"] = [u for u in existing["units"]
                             if u.get("id") != unit_id]
        existing["units"].append(unit)
        units_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"Saved to {units_path}", err=True)


@debug.command("describe-data")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--root", "-r", default=".", type=click.Path(exists=True),
              help="Project root (file path is relative to this)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print formatted prompt without invoking claude")
@click.option("--save", is_flag=True, default=False,
              help="Append/replace this unit in .winkers/units.json")
def describe_data(file_path: str, root: str, dry_run: bool, save: bool):
    """Generate a description for one data file (JSON / YAML / TOML).

    \b
    Example:
      winkers debug describe-data data/tespy_topology.json -r /path/to/project
    """
    import json as _json

    from winkers.data_files.scanner import read_data_file
    from winkers.descriptions.author import author_data_file_description
    from winkers.descriptions.prompts import format_data_file_prompt
    from winkers.descriptions.store import data_file_hash

    root_path = Path(root).resolve()
    fp = Path(file_path).resolve()
    entry = read_data_file(fp, root_path)
    if entry is None:
        click.echo(
            f"Cannot read or file too large: {fp} "
            f"(size cap defined in winkers.data_files.scanner.MAX_FILE_BYTES)",
            err=True,
        )
        raise SystemExit(2)

    if dry_run:
        click.echo(format_data_file_prompt(entry.content, entry.rel_path))
        return

    click.echo(
        f"Generating description for data:{entry.rel_path}...", err=True,
    )
    desc = author_data_file_description(
        file_content=entry.content,
        file_path=entry.rel_path,
        cwd=root_path,
    )
    if desc is None:
        click.echo("Description generation failed — see logs above.", err=True)
        raise SystemExit(1)

    unit_id = f"data:{entry.rel_path}"
    unit = {
        "id": unit_id,
        "kind": "traceability_unit",
        "name": f"Data file {entry.rel_path}",
        "source_files": [entry.rel_path],
        "source_anchors": [entry.rel_path],
        "source_hash": data_file_hash(entry.content),
        "description": desc.description,
        "hardcoded_artifacts": [
            a.model_dump(exclude_none=True)
            for a in desc.hardcoded_artifacts
        ],
    }
    click.echo(_json.dumps(unit, ensure_ascii=False, indent=2))

    if save:
        units_path = root_path / ".winkers" / "units.json"
        existing = {"units": []}
        if units_path.exists():
            try:
                existing = _json.loads(units_path.read_text(encoding="utf-8"))
                existing.setdefault("units", [])
            except Exception:
                pass
        existing["units"] = [u for u in existing["units"]
                             if u.get("id") != unit_id]
        existing["units"].append(unit)
        units_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"Saved to {units_path}", err=True)


@debug.command("intent-eval")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--sample", "-n", default=20, help="Number of functions to sample.")
@click.option("--prompt", "prompt_override", default=None, type=str,
              help="Test an alternative prompt template.")
@click.option("--compare", is_flag=True, default=False,
              help="Compare existing intents with freshly generated ones.")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Output as JSON.")
def intent_eval(path: str, sample: int, prompt_override: str | None,
                compare: bool, as_json: bool):
    """Evaluate intent generation quality.

    Requires a configured provider: ANTHROPIC_API_KEY (default)
    or --ollama in .winkers/config.toml.

    \b
    Examples:
        winkers debug intent-eval --sample 10 --json
        winkers debug intent-eval --prompt "Describe this function:" --json
        winkers debug intent-eval --compare
    """
    from winkers.intent.eval_cli import compare_intents, eval_intents
    from winkers.intent.provider import auto_detect, load_config

    root = Path(path).resolve()
    store = GraphStore(root)
    graph = store.load()
    if graph is None:
        click.echo("Error: graph not built. Run 'winkers init' first.", err=True)
        raise SystemExit(1)

    config = load_config(root)
    provider = auto_detect(config)

    from winkers.intent.provider import NoneProvider
    if isinstance(provider, NoneProvider):
        click.echo("Error: no LLM provider available.", err=True)
        raise SystemExit(1)

    if compare:
        results = compare_intents(graph, root, provider, sample=sample)
        if as_json:
            click.echo(json.dumps(results, indent=2))
        else:
            for r in results:
                changed = "CHANGED" if r["changed"] else "same"
                click.echo(f"  [{changed}] {r['name']}")
                click.echo(f"    current: {r['current']}")
                click.echo(f"    new:     {r['new']}")
        return

    results = eval_intents(
        graph, root, provider,
        sample=sample, prompt_override=prompt_override,
    )

    if as_json:
        click.echo(json.dumps(results, indent=2))
    else:
        for r in results:
            click.echo(f"  {r['name']} ({r['file']})")
            click.echo(f"    sig:    {r['signature']}")
            click.echo(f"    intent: {r['generated_intent']}")
            click.echo()
        click.echo(f"  {len(results)} functions evaluated.")
