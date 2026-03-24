"""CrossFileResolver — links call sites to function definitions across files."""

from __future__ import annotations

from winkers.languages import get_profile_for_file
from winkers.models import CallEdge, CallSite, Graph
from winkers.parser import TreeSitterParser


class CrossFileResolver:
    def __init__(self) -> None:
        self._parser = TreeSitterParser()

    def resolve(self, graph: Graph, root_path: str) -> None:
        """Populate graph.call_edges by resolving call sites to function defs."""
        from pathlib import Path

        root = Path(root_path)

        for fn_id, fn in graph.functions.items():
            file_node = graph.files.get(fn.file)
            if file_node is None:
                continue

            profile = get_profile_for_file(fn.file)
            if profile is None:
                continue

            src_path = root / fn.file
            if not src_path.exists():
                continue

            try:
                parse_result = self._parser.parse_file(src_path, profile)
            except Exception:
                continue

            captures = self._parser.query(parse_result, profile.call_query)

            for node, capture_name in captures:
                if capture_name not in ("call.name", "call.attr"):
                    continue

                # Only consider calls inside this function's line range
                call_line = node.start_point[0] + 1
                if not (fn.line_start <= call_line <= fn.line_end):
                    continue

                callee_name = parse_result.text(node)
                expression = self._get_call_expression(node, parse_result)

                target_fn_id, confidence = self._find_target(
                    callee_name, fn.file, file_node.imports, graph
                )
                if target_fn_id is None:
                    continue

                edge = CallEdge(
                    source_fn=fn_id,
                    target_fn=target_fn_id,
                    call_site=CallSite(
                        caller_fn_id=fn_id,
                        file=fn.file,
                        line=call_line,
                        expression=expression,
                    ),
                    confidence=confidence,
                )
                graph.call_edges.append(edge)

        # Upgrade confidence for calls that match imports
        self._upgrade_confidence_via_imports(graph)

        graph.meta["total_call_edges"] = len(graph.call_edges)

    def _get_call_expression(self, node, parse_result) -> str:
        """Walk up to the call node to get the full expression text."""
        parent = node.parent
        while parent is not None:
            if parent.type == "call":
                return parse_result.text(parent)[:120]
            parent = parent.parent
        return parse_result.text(node)

    def _upgrade_confidence_via_imports(self, graph: Graph) -> None:
        """If source file imports from target file, upgrade edge confidence."""
        # Build lookup: (source_file, target_file) → imported names
        import_map: dict[tuple[str, str], set[str]] = {}
        for edge in graph.import_edges:
            key = (edge.source_file, edge.target_file)
            import_map.setdefault(key, set()).update(edge.names)

        for edge in graph.call_edges:
            if edge.confidence >= 0.9:
                continue
            source_fn = graph.functions.get(edge.source_fn)
            target_fn = graph.functions.get(edge.target_fn)
            if not source_fn or not target_fn:
                continue
            key = (source_fn.file, target_fn.file)
            if key in import_map:
                if target_fn.name in import_map[key]:
                    edge.confidence = 0.95  # direct name import
                else:
                    edge.confidence = max(edge.confidence, 0.85)  # module import

    def _find_target(
        self,
        callee_name: str,
        source_file: str,
        imports: list[dict],
        graph: Graph,
    ) -> tuple[str | None, float]:
        """Return (fn_id, confidence) for best match, or (None, 0)."""

        # 1. Direct import match (confidence 1.0)
        for imp in imports:
            if imp.get("text") == callee_name and imp.get("capture") == "imp.name":
                module_text = next(
                    (i["text"] for i in imports if i.get("capture") == "imp.module"), ""
                )
                for fn_id, fn in graph.functions.items():
                    if fn.name == callee_name and module_text in fn.file:
                        return fn_id, 1.0

        # 2. Same-file match (confidence 0.95)
        same_file_id = f"{source_file}::{callee_name}"
        if same_file_id in graph.functions:
            return same_file_id, 0.95

        # 3. Project-wide unique name match (confidence 0.5)
        matches = [fn_id for fn_id, fn in graph.functions.items() if fn.name == callee_name]
        if len(matches) == 1:
            return matches[0], 0.5
        if len(matches) > 1:
            # Ambiguous — skip (too noisy)
            return None, 0.0

        return None, 0.0
