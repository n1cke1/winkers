"""Phase 1 description-first units pipeline for `winkers init`.

`_run_units_pipeline` is the orchestrator: scan templates + data files,
identify stale function/template/data units (ast_hash / content_hash
mismatch), re-describe via `claude --print` subprocess, prune orphans,
run coupling aggregator, and re-embed only changed units.

`_author_meta_unit_descriptions` is a follow-up pass that authors
class / attribute / value descriptions (Wave 4c-2) — structural unit
shells get LLM-prose descriptions so find_work_area can rank them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click


def _run_units_pipeline(root: Path, graph, force: bool = False,
                        concurrency: int = 1) -> dict:
    """Phase 1 description-first units pipeline.

    1. Scan templates for sections (winkers/templates/scanner.py).
    2. Identify stale function_units (ast_hash mismatch) and stale
       template sections (content_hash mismatch).
    3. Re-describe stale units via `claude --print` subprocess
       (subscription auth, sequential — concurrency risks rate limits).
    4. Prune orphan units (graph fn no longer exists, section disappeared).
    5. Run coupling aggregator over all units → traceability_units.
    6. Re-embed only changed units (BGE-M3, incremental hash check).

    Returns a stats dict for the caller's progress report.
    """
    from winkers.data_files.scanner import (
        discover_data_files,
        read_data_file,
    )
    from winkers.descriptions.aggregator import (
        detect_couplings,
        proposed_to_unit,
    )
    from winkers.descriptions.author import (
        author_data_file_description,
        author_function_description,
        author_template_description,
    )
    from winkers.descriptions.store import (
        UnitsStore,
        data_file_hash,
        section_hash,
    )
    from winkers.embeddings import (
        INDEX_FILENAME,
        embed_units,
        load_index,
        save_index,
    )
    from winkers.templates.scanner import scan_project

    store = UnitsStore(root)
    units = store.load()
    stats = {
        "fn_described": 0, "fn_failed": 0,
        "template_described": 0, "template_failed": 0,
        "data_described": 0, "data_failed": 0,
        "couplings": 0,
        "embed_reused": 0, "embed_encoded": 0,
    }

    # ── 1. Scan templates ────────────────────────────────────────────────
    sections = scan_project(root)  # filtered to leaves by scanner
    live_template_ids = {f"template:{s.file}#{s.id}" for s in sections}
    section_by_uid = {f"template:{s.file}#{s.id}": s for s in sections}

    # ── 1b. Scan data files (JSON/YAML) ─────────────────────────────────
    data_paths = discover_data_files(root)
    data_entries: list = []
    for p in data_paths:
        e = read_data_file(p, root)
        if e is not None:
            data_entries.append(e)
    live_data_ids = {f"data:{e.rel_path}" for e in data_entries}
    data_by_uid = {f"data:{e.rel_path}": e for e in data_entries}

    # ── 2. Identify stale ────────────────────────────────────────────────
    graph_fn_summary = {
        fn.id: {"ast_hash": fn.ast_hash}
        for fn in graph.functions.values()
    }
    live_fn_ids = set(graph_fn_summary.keys())
    if force:
        stale_fn_ids = live_fn_ids
        stale_tpl_ids = live_template_ids
        stale_data_ids = live_data_ids
    else:
        stale_fn_ids = store.stale_function_units(units, graph_fn_summary)
        stale_tpl_ids = store.stale_template_units(units, sections)
        stale_data_ids = store.stale_data_file_units(units, data_entries)

    click.echo(
        f"  {len(stale_fn_ids)} function unit(s), "
        f"{len(stale_tpl_ids)} template section(s), "
        f"{len(stale_data_ids)} data file(s) need description"
    )

    # ── 3. Author descriptions for stale function units ────────────────
    if stale_fn_ids:
        # Wave 4d: function_units that the impact pass already enriched
        # carry `description` directly on the unit dict. Bring them up
        # to spec (anchor + source_hash) without a second LLM call.
        # Anything still missing falls through to the legacy
        # `author_function_description` route below.
        existing_by_id = {u.get("id"): u for u in units if u.get("id")}

        reused_from_impact = 0
        still_stale: set[str] = set()
        for fn_id in stale_fn_ids:
            existing = existing_by_id.get(fn_id)
            if not existing or not existing.get("description"):
                still_stale.add(fn_id)
                continue
            fn = graph.functions.get(fn_id)
            if fn is None:
                still_stale.add(fn_id)
                continue
            display_name = (
                f"{fn.class_name}.{fn.name}" if fn.class_name else fn.name
            )
            # Merge graph anchor / source_hash onto the impact-authored
            # unit (impact pass writes a partial stub).
            existing.setdefault("kind", "function_unit")
            existing["name"] = display_name
            existing["anchor"] = {
                "file": fn.file,
                "fn": fn.name,
                **({"class": fn.class_name} if fn.class_name else {}),
            }
            existing["source_hash"] = fn.ast_hash
            reused_from_impact += 1
            stats["fn_described"] += 1
        if reused_from_impact:
            store.save(units)
            click.echo(
                f"  Reused {reused_from_impact} description(s) from impact pass."
            )

        # Build per-fn contexts up front (sync, cheap) so the parallel
        # phase only does the slow work (`claude --print`).
        fn_contexts: list[dict] = []
        for fn_id in sorted(still_stale):
            fn = graph.functions.get(fn_id)
            if fn is None:
                continue
            src_path = root / fn.file
            if not src_path.exists():
                stats["fn_failed"] += 1
                continue
            src_lines = src_path.read_text(encoding="utf-8").splitlines()
            fn_source = "\n".join(
                src_lines[fn.line_start - 1: fn.line_end]
            )
            caller_sigs: list[str] = []
            seen: set[str] = set()
            for edge in graph.call_edges:
                if edge.target_fn != fn_id or edge.source_fn in seen:
                    continue
                seen.add(edge.source_fn)
                c = graph.functions.get(edge.source_fn)
                if c is None:
                    continue
                params = ", ".join(p.name for p in c.params)
                prefix = f"{c.class_name}." if c.class_name else ""
                caller_sigs.append(f"def {prefix}{c.name}({params})")
                if len(caller_sigs) >= 2:
                    break
            display_name = (
                f"{fn.class_name}.{fn.name}" if fn.class_name else fn.name
            )
            fn_contexts.append({
                "fn_id": fn_id, "fn": fn, "fn_source": fn_source,
                "display_name": display_name, "caller_sigs": caller_sigs,
            })

        # Run `claude --print` calls in a thread pool. Each subprocess
        # blocks on its own stdin/stdout, so threads are fine (no GIL
        # contention on subprocess.run). Concurrency caps shared
        # subscription rate-limit pressure — recommended ≤4.
        def _describe_fn(ctx):
            return ctx, author_function_description(
                fn_source=ctx["fn_source"],
                file_path=ctx["fn"].file,
                fn_name=ctx["display_name"],
                callers=ctx["caller_sigs"],
                cwd=root,
            )

        with click.progressbar(
            length=len(fn_contexts), label="Function descriptions",
        ) as bar:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = [ex.submit(_describe_fn, c) for c in fn_contexts]
                for fut in as_completed(futures):
                    ctx, desc = fut.result()
                    if desc is None:
                        stats["fn_failed"] += 1
                    else:
                        fn = ctx["fn"]
                        unit = {
                            "id": ctx["fn_id"],
                            "kind": "function_unit",
                            "name": ctx["display_name"],
                            "anchor": {
                                "file": fn.file,
                                "fn": fn.name,
                                **({"class": fn.class_name}
                                   if fn.class_name else {}),
                            },
                            "source_hash": fn.ast_hash,
                            "description": desc.description,
                            "hardcoded_artifacts": [
                                a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts
                            ],
                        }
                        # Upsert is single-threaded — only the main thread
                        # runs as_completed callbacks, no race on `units`.
                        units = store.upsert(units, unit)
                        # Persist after each unit so an interrupted run
                        # leaves a usable partial index (resumable on next
                        # `init --with-units`).
                        store.save(units)
                        stats["fn_described"] += 1
                    bar.update(1)

    # ── 4. Author descriptions for stale template sections ─────────────
    if stale_tpl_ids:
        tpl_contexts: list[dict] = []
        for uid in sorted(stale_tpl_ids):
            sec = section_by_uid.get(uid)
            if sec is None:
                continue
            neighbors = [s.id for s in sections if s.id != sec.id][:5]
            tpl_contexts.append({"uid": uid, "sec": sec, "neighbors": neighbors})

        def _describe_tpl(ctx):
            sec = ctx["sec"]
            return ctx, author_template_description(
                section_html=sec.content,
                file_path=sec.file,
                section_id=sec.id,
                leading_comment=sec.leading_comment,
                neighbor_section_ids=ctx["neighbors"],
                cwd=root,
            )

        with click.progressbar(
            length=len(tpl_contexts), label="Template descriptions",
        ) as bar:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = [ex.submit(_describe_tpl, c) for c in tpl_contexts]
                for fut in as_completed(futures):
                    ctx, desc = fut.result()
                    if desc is None:
                        stats["template_failed"] += 1
                    else:
                        sec = ctx["sec"]
                        unit = {
                            "id": ctx["uid"],
                            "kind": "traceability_unit",
                            "name": f"Section #{sec.id} ({sec.file})",
                            "source_files": [sec.file],
                            "source_anchors": [f"{sec.file}#{sec.id}"],
                            "source_hash": section_hash(sec.content),
                            "description": desc.description,
                            "hardcoded_artifacts": [
                                a.model_dump(exclude_none=True)
                                for a in desc.hardcoded_artifacts
                            ],
                        }
                        units = store.upsert(units, unit)
                        store.save(units)
                        stats["template_described"] += 1
                    bar.update(1)

    # ── 4b. Author descriptions for stale data files ────────────────────
    if stale_data_ids:
        data_contexts: list[dict] = []
        for uid in sorted(stale_data_ids):
            entry = data_by_uid.get(uid)
            if entry is None:
                continue
            data_contexts.append({"uid": uid, "entry": entry})

        def _describe_data(ctx):
            entry = ctx["entry"]
            return ctx, author_data_file_description(
                file_content=entry.content,
                file_path=entry.rel_path,
                cwd=root,
            )

        with click.progressbar(
            length=len(data_contexts), label="Data file descriptions",
        ) as bar:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = [ex.submit(_describe_data, c) for c in data_contexts]
                for fut in as_completed(futures):
                    ctx, desc = fut.result()
                    if desc is None:
                        stats["data_failed"] += 1
                    else:
                        entry = ctx["entry"]
                        unit = {
                            "id": ctx["uid"],
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
                        units = store.upsert(units, unit)
                        store.save(units)
                        stats["data_described"] += 1
                    bar.update(1)

    # ── 4c. Promote value_locked_collections into units (Wave 4b) ──────
    # Structural only — no LLM. The summary embeds value names so
    # find_work_area / orient(task) can match queries like "status enum"
    # against the collection. LLM-authored description lands in 4c.
    from winkers.value_locked import build_value_units
    value_units = build_value_units(graph, root)
    live_value_ids = {u["id"] for u in value_units}
    for vu in value_units:
        units = store.upsert(units, vu)

    # ── 4d. Promote classes + class attributes into units (Wave 5a) ────
    # Structural only — class_unit + attribute_unit kinds. Lets
    # `find_work_area` rank Client model in "audit Client class" and
    # before_create resolve `Client.invoices` to a real graph node.
    from winkers.class_attrs import build_attribute_units, build_class_units
    class_units = build_class_units(graph, root)
    attribute_units = build_attribute_units(graph, root)
    live_class_ids = {u["id"] for u in class_units}
    live_attr_ids = {u["id"] for u in attribute_units}
    for cu in class_units:
        units = store.upsert(units, cu)
    for au in attribute_units:
        units = store.upsert(units, au)

    # ── 4e. LLM-author class / attribute / value descriptions (Wave 4c-2)
    # Structural unit dicts have description="". Subscription-path
    # `claude --print` fills in 70-120w prose + hardcoded_artifacts so
    # find_work_area can match domain-language queries against these
    # kinds. Skipped silently when claude binary unavailable; never
    # blocks init.
    units, n_class, n_attr, n_val = _author_meta_unit_descriptions(
        units, store, graph, value_units, class_units, attribute_units,
        root=root, concurrency=concurrency,
    )
    if n_class or n_attr or n_val:
        click.echo(
            f"  Authored: {n_class} class / {n_attr} attribute / "
            f"{n_val} value description(s)."
        )

    # ── 5. Prune orphans ────────────────────────────────────────────────
    units = store.prune_orphans(
        units, live_fn_ids, live_template_ids, live_data_ids,
        live_value_ids=live_value_ids,
        live_class_ids=live_class_ids,
        live_attr_ids=live_attr_ids,
    )

    # ── 6. Coupling aggregator ──────────────────────────────────────────
    # Re-detect from primary units (exclude prior auto-detected couplings
    # so we don't bootstrap couplings from couplings).
    primary = [
        u for u in units
        if u.get("hardcoded_artifacts")
        and (u.get("meta") or {}).get("origin") != "auto-detected"
    ]
    clusters = detect_couplings(primary)
    units = [
        u for u in units
        if (u.get("meta") or {}).get("origin") != "auto-detected"
    ]
    units.extend(proposed_to_unit(c) for c in clusters)
    stats["couplings"] = len(clusters)

    # ── 7. Save units.json ──────────────────────────────────────────────
    store.save(units)

    # ── 8. Embeddings (incremental) ─────────────────────────────────────
    idx_path = root / ".winkers" / INDEX_FILENAME
    existing_idx = load_index(idx_path)
    new_idx, embed_stats = embed_units(units, existing=existing_idx, force=force)
    save_index(new_idx, idx_path)
    stats["embed_reused"] = embed_stats["reused"]
    stats["embed_encoded"] = embed_stats["encoded"]

    return stats


def _author_meta_unit_descriptions(
    units: list[dict],
    store,
    graph,
    value_units: list[dict],
    class_units: list[dict],
    attribute_units: list[dict],
    *,
    root: Path,
    concurrency: int,
) -> tuple[list[dict], int, int, int]:
    """Run the subscription-path author for class / attribute / value units.

    Each kind gets its own author call sharing the same `claude --print`
    transport. Failures are silently skipped (no API binary configured,
    timeout, malformed response) — meta-unit descriptions are nice-to-
    have, never gate init.

    Returns updated `units` list and per-kind authored counts.
    """
    from winkers.descriptions.author import (
        author_attribute_description,
        author_class_description,
        author_value_description,
    )

    # ── classes ────────────────────────────────────────────────────────
    # Build context per stale class_unit. Method signatures pulled from
    # graph.functions (already kept up to date). Attribute lines pulled
    # from graph.class_attributes' `source_line` cache — but the
    # scanner stores only line numbers, so reread the file. Cheap: each
    # class is read once per init.
    by_id = {u.get("id"): u for u in units if u.get("id")}
    file_text_cache: dict[str, str] = {}

    def _read_file_text(rel: str) -> str:
        cached = file_text_cache.get(rel)
        if cached is None:
            try:
                cached = (root / rel).read_text(
                    encoding="utf-8", errors="replace",
                )
            except OSError:
                cached = ""
            file_text_cache[rel] = cached
        return cached

    class_contexts: list[dict] = []
    for cu in class_units:
        existing = by_id.get(cu["id"]) or cu
        if existing.get("description"):
            continue
        anchor = cu.get("anchor") or {}
        file_path = anchor.get("file", "")
        line_start = anchor.get("line", 1)
        # method signatures from graph.functions matching this class
        method_sigs: list[str] = []
        for fn in graph.functions.values():
            if fn.file != file_path or fn.class_name != cu["name"]:
                continue
            params = ", ".join(
                f"{p.name}: {p.type_hint}" if p.type_hint else p.name
                for p in fn.params
            )
            method_sigs.append(f"def {fn.name}({params})")
            if len(method_sigs) >= 12:
                break
        # attribute lines for this class
        text = _read_file_text(file_path)
        lines = text.splitlines() if text else []
        attribute_lines: list[str] = []
        for ca in graph.class_attributes:
            if ca.file != file_path or ca.class_name != cu["name"]:
                continue
            if 0 < ca.line <= len(lines):
                attribute_lines.append(lines[ca.line - 1])
        # bracket the class to estimate line_end (anchor only stores
        # line_start currently; reread the class slice for the prompt
        # source ceiling). Walk graph.class_definitions for the match.
        line_end = line_start
        for cd in graph.class_definitions:
            if cd.file == file_path and cd.name == cu["name"]:
                line_end = cd.line_end
                break
        class_contexts.append({
            "uid": cu["id"],
            "name": cu["name"],
            "file": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "base_classes": cu.get("base_classes", []),
            "method_sigs": method_sigs,
            "attribute_lines": attribute_lines,
        })

    n_class = 0
    if class_contexts:
        def _describe_cls(ctx):
            return ctx, author_class_description(
                class_name=ctx["name"],
                file_path=ctx["file"],
                line_start=ctx["line_start"],
                line_end=ctx["line_end"],
                base_classes=ctx["base_classes"],
                method_signatures=ctx["method_sigs"],
                attribute_lines=ctx["attribute_lines"],
                cwd=root,
            )

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futures = [ex.submit(_describe_cls, c) for c in class_contexts]
            for fut in as_completed(futures):
                ctx, desc = fut.result()
                if desc is None:
                    continue
                target = by_id.get(ctx["uid"])
                if target is None:
                    continue
                target["description"] = desc.description
                if desc.hardcoded_artifacts:
                    target["hardcoded_artifacts"] = [
                        a.model_dump(exclude_none=True)
                        for a in desc.hardcoded_artifacts
                    ]
                n_class += 1
        store.save(units)

    # ── attributes ─────────────────────────────────────────────────────
    attr_contexts: list[dict] = []
    for au in attribute_units:
        existing = by_id.get(au["id"]) or au
        if existing.get("description"):
            continue
        anchor = au.get("anchor") or {}
        file_path = anchor.get("file", "")
        line = anchor.get("line", 1)
        text = _read_file_text(file_path)
        lines = text.splitlines() if text else []
        source_line = ""
        if 0 < line <= len(lines):
            source_line = lines[line - 1]
        # Owning class summary if its description is already authored.
        class_summary = ""
        cls_uid = f"class:{file_path}::{au.get('class_name', '')}"
        cls_unit = by_id.get(cls_uid)
        if cls_unit:
            class_summary = (
                cls_unit.get("description")
                or cls_unit.get("summary")
                or ""
            )[:200]
        attr_contexts.append({
            "uid": au["id"],
            "name": au["name"],
            "class_name": au.get("class_name", ""),
            "file": file_path,
            "line": line,
            "ctor": au.get("ctor", ""),
            "annotation": au.get("annotation", ""),
            "source_line": source_line,
            "class_summary": class_summary,
        })

    n_attr = 0
    if attr_contexts:
        def _describe_attr(ctx):
            return ctx, author_attribute_description(
                name=ctx["name"],
                class_name=ctx["class_name"],
                file_path=ctx["file"],
                line=ctx["line"],
                ctor=ctx["ctor"],
                annotation=ctx["annotation"],
                source_line=ctx["source_line"],
                class_summary=ctx["class_summary"],
                cwd=root,
            )

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futures = [ex.submit(_describe_attr, c) for c in attr_contexts]
            for fut in as_completed(futures):
                ctx, desc = fut.result()
                if desc is None:
                    continue
                target = by_id.get(ctx["uid"])
                if target is None:
                    continue
                target["description"] = desc.description
                if desc.hardcoded_artifacts:
                    target["hardcoded_artifacts"] = [
                        a.model_dump(exclude_none=True)
                        for a in desc.hardcoded_artifacts
                    ]
                n_attr += 1
        store.save(units)

    # ── values ─────────────────────────────────────────────────────────
    value_contexts: list[dict] = []
    for vu in value_units:
        existing = by_id.get(vu["id"]) or vu
        if existing.get("description"):
            continue
        anchor = vu.get("anchor") or {}
        value_contexts.append({
            "uid": vu["id"],
            "name": vu["name"],
            "file": anchor.get("file", ""),
            "line": anchor.get("line", 1),
            "kind": _value_unit_kind_from_collection(graph, vu),
            "values": vu.get("values", []),
            "consumer_count": vu.get("consumer_count", 0),
            "consumer_files": vu.get("consumer_files", []),
        })

    n_val = 0
    if value_contexts:
        def _describe_val(ctx):
            return ctx, author_value_description(
                name=ctx["name"],
                file_path=ctx["file"],
                line=ctx["line"],
                kind=ctx["kind"],
                values=ctx["values"],
                consumer_count=ctx["consumer_count"],
                consumer_files=ctx["consumer_files"],
                cwd=root,
            )

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futures = [ex.submit(_describe_val, c) for c in value_contexts]
            for fut in as_completed(futures):
                ctx, desc = fut.result()
                if desc is None:
                    continue
                target = by_id.get(ctx["uid"])
                if target is None:
                    continue
                target["description"] = desc.description
                if desc.hardcoded_artifacts:
                    target["hardcoded_artifacts"] = [
                        a.model_dump(exclude_none=True)
                        for a in desc.hardcoded_artifacts
                    ]
                n_val += 1
        store.save(units)

    return units, n_class, n_attr, n_val


def _value_unit_kind_from_collection(graph, value_unit: dict) -> str:
    """Resolve the collection ``kind`` (set/frozenset/list/Enum) from graph."""
    anchor = value_unit.get("anchor") or {}
    file = anchor.get("file", "")
    name = value_unit.get("name", "")
    for col in graph.value_locked_collections:
        if col.file == file and col.name == name:
            return col.kind
    return "set"
