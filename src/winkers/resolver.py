"""CrossFileResolver — links call sites to function definitions across files."""

from __future__ import annotations

from winkers.languages import get_profile_for_file
from winkers.models import CallEdge, CallSite, Graph
from winkers.parser import TreeSitterParser


class CrossFileResolver:
    # Confidence for heuristic self.<attr>.method() edges. Below "direct
    # import" (0.9+) and "same-file" (0.95) — we only traced type through
    # __init__ assignment, not through an import declaration.
    SELF_ATTR_CONFIDENCE = 0.85

    def __init__(self) -> None:
        self._parser = TreeSitterParser()
        self._self_attr_resolved = 0
        self._self_attr_skipped = 0

    def resolve(self, graph: Graph, root_path: str) -> None:
        """Populate graph.call_edges by resolving call sites to function defs."""
        from pathlib import Path

        root = Path(root_path)
        self._self_attr_resolved = 0
        self._self_attr_skipped = 0

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

            imported_modules = {
                i["text"] for i in file_node.imports
                if i.get("capture") == "imp.module"
            }

            for node, capture_name in captures:
                if capture_name not in ("call.name", "call.attr"):
                    continue

                # Only consider calls inside this function's line range
                call_line = node.start_point[0] + 1
                if not (fn.line_start <= call_line <= fn.line_end):
                    continue

                callee_name = parse_result.text(node)

                # For attribute calls (obj.method), decide the resolution path:
                #   - obj is an imported module       → fall through to name
                #     resolution below (existing behaviour)
                #   - obj is `self.<attr>` and we have class metadata → try
                #     the heuristic resolver (new in 0.8.x)
                #   - anything else (list.append, etc.) → skip to avoid noise
                if capture_name == "call.attr":
                    attr_parent = node.parent
                    if attr_parent and attr_parent.type == "attribute":
                        obj_node = attr_parent.child_by_field_name("object")
                        if obj_node:
                            obj_text = parse_result.text(obj_node)
                            if obj_text not in imported_modules:
                                heuristic = self._try_self_attr_resolve(
                                    obj_text, callee_name, fn, graph,
                                )
                                if heuristic is not None:
                                    target_fn_id, confidence = heuristic
                                    expression = self._get_call_expression(
                                        node, parse_result,
                                    )
                                    graph.call_edges.append(CallEdge(
                                        source_fn=fn_id,
                                        target_fn=target_fn_id,
                                        call_site=CallSite(
                                            caller_fn_id=fn_id,
                                            file=fn.file,
                                            line=call_line,
                                            expression=expression,
                                        ),
                                        confidence=confidence,
                                    ))
                                    self._self_attr_resolved += 1
                                else:
                                    if obj_text.startswith("self."):
                                        self._self_attr_skipped += 1
                                continue

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
        graph.meta["self_attr_resolved"] = self._self_attr_resolved
        graph.meta["self_attr_skipped"] = self._self_attr_skipped

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

    def _try_self_attr_resolve(
        self,
        obj_text: str,
        method_name: str,
        caller_fn,
        graph: Graph,
    ) -> tuple[str, float] | None:
        """Resolve `self.<attr>.<method>()` via class metadata.

        Returns (target_fn_id, confidence) or None if any lookup fails.
        Does NOT walk inheritance chains (out of MVP scope) — a subclass
        method that merely inherits `create` from its base will not resolve.
        """
        if not obj_text.startswith("self."):
            return None
        if caller_fn.class_name is None:
            return None
        attr = obj_text[len("self."):]
        # Multi-level access (self.a.b.method) — out of scope.
        if "." in attr:
            return None
        class_name = caller_fn.class_name
        attr_map = graph.class_attr_types.get(class_name)
        if not attr_map:
            return None
        target_class = attr_map.get(attr)
        if target_class is None:
            return None
        target_file = graph.class_files.get(target_class)
        if target_file is None:
            return None
        target_fn_id = f"{target_file}::{method_name}"
        if target_fn_id not in graph.functions:
            return None
        return target_fn_id, self.SELF_ATTR_CONFIDENCE

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
