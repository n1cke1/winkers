"""GraphStore — save, load, and incrementally update the graph."""

from __future__ import annotations

import json
from pathlib import Path

from winkers.models import Graph

STORE_DIR = ".winkers"
GRAPH_FILE = "graph.json"


class GraphStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store_dir = root / STORE_DIR
        self.graph_path = self.store_dir / GRAPH_FILE

    def save(self, graph: Graph) -> None:
        self.store_dir.mkdir(exist_ok=True)
        # Truncate long expressions before saving
        for edge in graph.call_edges:
            if len(edge.call_site.expression) > 80:
                edge.call_site.expression = edge.call_site.expression[:80] + "..."
        self.graph_path.write_text(
            graph.model_dump_json(indent=2, exclude_defaults=True), encoding="utf-8"
        )

    def load(self) -> Graph | None:
        if not self.graph_path.exists():
            return None
        try:
            data = json.loads(self.graph_path.read_text(encoding="utf-8"))
            return Graph.model_validate(data)
        except Exception:
            return None

    def exists(self) -> bool:
        return self.graph_path.exists()

    def update_files(self, graph: Graph, changed_files: list[str]) -> Graph:
        """Incremental update: reparse only changed files."""
        from winkers.graph import GraphBuilder
        from winkers.resolver import CrossFileResolver

        builder = GraphBuilder()

        for rel in changed_files:
            # Remove old data for these files
            old_fn_ids = graph.files.get(rel, None)
            if old_fn_ids:
                for fn_id in old_fn_ids.function_ids:
                    graph.functions.pop(fn_id, None)
                del graph.files[rel]

            # Remove stale edges touching these files
            graph.call_edges = [
                e for e in graph.call_edges
                if e.call_site.file not in changed_files
            ]
            graph.import_edges = [
                e for e in graph.import_edges
                if e.source_file not in changed_files
            ]

        # Drop class metadata for changed files — will be rebuilt by _parse_file.
        changed_set = set(changed_files)
        stale_classes = [
            cls for cls, f in graph.class_files.items() if f in changed_set
        ]
        for cls in stale_classes:
            graph.class_files.pop(cls, None)
            graph.class_attr_types.pop(cls, None)

        # Reparse changed files
        for rel in changed_files:
            from winkers.languages import get_profile_for_file
            profile = get_profile_for_file(rel)
            if profile:
                builder._parse_file(self.root / rel, rel, profile, graph)  # type: ignore[attr-defined]

        # Re-resolve (full, simpler than partial for now)
        CrossFileResolver().resolve(graph, str(self.root))

        # Compute AST hashes for new/modified functions
        self._compute_ast_hashes(graph, changed_files)

        # Refresh value_locked collections — needs call_edges in place.
        from winkers.value_locked import detect_value_locked
        detect_value_locked(graph, self.root)

        # Prune stale impact.json entries (no LLM call — stale entries just
        # stop being returned; real regeneration is a separate `winkers init`).
        try:
            from winkers.impact import ImpactStore
            impact_store = ImpactStore(self.root)
            impact = impact_store.load()
            if impact.functions:
                removed = ImpactStore.prune(impact, set(graph.functions.keys()))
                if removed:
                    impact_store.save(impact)
        except Exception:
            pass

        return graph

    def _compute_ast_hashes(self, graph: Graph, files: list[str]) -> None:
        """Compute ast_hash for all functions in the given files."""
        from winkers.detection.duplicates import compute_ast_hash

        for rel in files:
            file_path = self.root / rel
            if not file_path.exists():
                continue
            source = file_path.read_bytes()
            file_node = graph.files.get(rel)
            if file_node is None:
                continue
            for fn_id in file_node.function_ids:
                fn = graph.functions.get(fn_id)
                if fn is None:
                    continue
                fn.ast_hash = compute_ast_hash(source, fn, fn.language)
