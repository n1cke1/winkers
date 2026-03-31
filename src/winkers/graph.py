"""GraphBuilder — builds a dependency graph from parsed source files."""

from __future__ import annotations

import re
import time
from pathlib import Path

from winkers.languages import get_profile_for_file
from winkers.languages.base import LanguageProfile
from winkers.models import FileNode, FunctionNode, Graph, ImportEdge, Param
from winkers.parser import ParseResult, TreeSitterParser

_VALID_IDENT = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _find_passthrough_prefixes(paths: list[str]) -> dict[str, str]:
    """For each top-level directory, find a passthrough prefix to strip.

    A directory is passthrough when it contains exactly one subdirectory
    and no source files at its level.  The function walks down until
    that condition breaks and returns the accumulated prefix.

    Returns ``{top_dir: prefix_with_trailing_slash}`` only for top dirs
    that actually have a passthrough chain.
    """
    by_top: dict[str, list[str]] = {}
    for p in paths:
        parts = p.split("/")
        if len(parts) > 1:
            by_top.setdefault(parts[0], []).append(p)

    prefixes: dict[str, str] = {}
    for top, files in by_top.items():
        prefix = top + "/"
        while True:
            subdirs: set[str] = set()
            has_files = False
            for f in files:
                rest = f[len(prefix):]
                rest_parts = rest.split("/")
                if len(rest_parts) == 1:
                    has_files = True
                else:
                    subdirs.add(rest_parts[0])
            if len(subdirs) == 1 and not has_files:
                prefix = prefix + next(iter(subdirs)) + "/"
            else:
                break
        if prefix != top + "/":
            prefixes[top] = prefix
    return prefixes


class GraphBuilder:
    def __init__(self) -> None:
        self._parser = TreeSitterParser()

    def build(self, root: Path) -> Graph:
        start = time.monotonic()
        graph = Graph()

        source_files = self._collect_files(root)

        for file_path in source_files:
            profile = get_profile_for_file(file_path.name)
            if profile is None:
                continue
            rel = file_path.relative_to(root).as_posix()
            self._parse_file(file_path, rel, profile, graph)

        self._assign_zones(graph)
        self._build_import_edges(graph)

        elapsed = (time.monotonic() - start) * 1000
        graph.meta = {
            "schema_version": "2",
            "languages": sorted({fn.language for fn in graph.functions.values()}),
            "total_files": len(graph.files),
            "total_functions": len(graph.functions),
            "total_call_edges": len(graph.call_edges),
            "parse_time_ms": round(elapsed, 1),
        }
        self._build_ui_map(graph, root)
        return graph

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assign_zones(self, graph: Graph) -> None:
        """Assign zone to each file, skipping passthrough directories.

        A passthrough directory contains exactly one subdirectory and no
        source files at its own level (e.g. ``src/winkers/``).  Files
        after the passthrough prefix get their zone from the next
        directory level; files sitting directly under the prefix get
        zone ``"core"``.
        """
        all_paths = [rel.replace("\\", "/") for rel in graph.files]
        prefixes = _find_passthrough_prefixes(all_paths)

        for rel, file_node in graph.files.items():
            path = rel.replace("\\", "/")
            parts = path.split("/")

            if len(parts) == 1:
                # Root file: use filename without extension
                file_node.zone = parts[0].rsplit(".", 1)[0]
                continue

            top = parts[0]
            prefix = prefixes.get(top)

            if prefix:
                stripped = path[len(prefix):]
                stripped_parts = stripped.split("/")
                if len(stripped_parts) > 1:
                    file_node.zone = stripped_parts[0]
                else:
                    file_node.zone = "core"
            else:
                file_node.zone = top

    def _build_import_edges(self, graph: Graph) -> None:
        """Build import edges from collected file imports."""
        # Map module stems to file paths
        stem_to_file: dict[str, str] = {}
        for rel in graph.files:
            # "modules/pricing.py" → stems: "pricing", "modules.pricing", "modules/pricing"
            parts = rel.replace("\\", "/").rsplit(".", 1)[0].split("/")
            stem_to_file[parts[-1]] = rel  # "pricing" → file
            stem_to_file[".".join(parts)] = rel  # "modules.pricing" → file
            stem_to_file["/".join(parts)] = rel  # "modules/pricing" → file

        STDLIB = {
            "os", "sys", "json", "re", "math", "time", "datetime", "collections",
            "functools", "itertools", "pathlib", "typing", "logging", "io",
            "abc", "copy", "enum", "hashlib", "hmac", "http", "importlib",
            "inspect", "operator", "shutil", "string", "subprocess", "tempfile",
            "textwrap", "threading", "traceback", "unittest", "urllib", "uuid",
            "warnings", "contextlib", "dataclasses", "decimal", "fractions",
            "glob", "gzip", "csv", "sqlite3", "socket", "ssl", "struct",
            "asyncio", "concurrent", "multiprocessing", "signal", "argparse",
            "configparser", "pprint", "statistics", "random", "secrets",
        }

        for source_file, file_node in graph.files.items():
            # Group imported names by target file
            edges: dict[str, list[str]] = {}
            for imp in file_node.imports:
                capture = imp.get("capture", "")
                text = imp.get("text", "")
                if not text:
                    continue

                if capture == "imp.module":
                    module_stem = text.split(".")[-1]
                    if module_stem in STDLIB or text.split(".")[0] in STDLIB:
                        continue
                    target = stem_to_file.get(module_stem) or stem_to_file.get(text)
                    if target and target != source_file:
                        edges.setdefault(target, [])

                elif capture == "imp.name":
                    # Find which file exports this name
                    for fid, fn in graph.functions.items():
                        if fn.name == text and fn.file != source_file:
                            edges.setdefault(fn.file, []).append(text)
                            break

            for target_file, names in edges.items():
                graph.import_edges.append(ImportEdge(
                    source_file=source_file,
                    target_file=target_file,
                    names=sorted(set(names)),
                ))

    def _collect_files(self, root: Path) -> list[Path]:
        IGNORE = {
            "node_modules", ".venv", "venv", "__pycache__",
            ".git", "dist", "build", "migrations",
        }
        result: list[Path] = []
        for p in root.rglob("*"):
            if p.is_file() and not any(part in IGNORE for part in p.parts):
                if get_profile_for_file(p.name) is not None:
                    result.append(p)
        return result

    def _parse_file(self, path: Path, rel: str, profile: LanguageProfile, graph: Graph) -> None:
        try:
            parse_result = self._parser.parse_file(path, profile)
        except Exception:
            return

        source_lines = parse_result.source.decode("utf-8", errors="replace").splitlines()
        loc = len(source_lines)

        imports = self._extract_imports(parse_result)
        fn_ids = self._extract_functions(parse_result, rel, graph)
        self._extract_routes(parse_result, rel, graph)

        # Skip files with no functions and no imports (likely unparseable)
        if not fn_ids and not imports:
            return

        graph.files[rel] = FileNode(
            path=rel,
            language=profile.language,
            imports=imports,
            function_ids=fn_ids,
            lines_of_code=loc,
        )

    def _extract_imports(self, parse_result: ParseResult) -> list[dict]:
        profile = parse_result.profile
        captures = self._parser.query_captures(parse_result, profile.import_query)
        imports: list[dict] = []
        seen: set[str] = set()
        for name, nodes in captures.items():
            for node in nodes:
                text = parse_result.text(node)
                key = f"{name}:{text}"
                if key not in seen:
                    seen.add(key)
                    imports.append({"capture": name, "text": text})
        return imports

    def _extract_functions(self, parse_result: ParseResult, rel: str, graph: Graph) -> list[str]:
        profile = parse_result.profile
        matches = self._parser.query_matches(parse_result, profile.function_query)

        fn_ids: list[str] = []
        seen_ids: set[str] = set()

        for _pattern_idx, capture_dict in matches:
            name_nodes = capture_dict.get("fn.name", [])
            def_nodes = capture_dict.get("fn.def", [])

            if not name_nodes or not def_nodes:
                continue

            name_node = name_nodes[0]
            def_node = def_nodes[0]

            name = parse_result.text(name_node)
            fn_id = f"{rel}::{name}"

            # Deduplicate (e.g. nested functions can appear in multiple matches)
            if fn_id in seen_ids:
                continue
            seen_ids.add(fn_id)

            params = self._extract_params(capture_dict.get("fn.params", []), parse_result)
            return_type = None
            rt_nodes = capture_dict.get("fn.return_type", [])
            if rt_nodes:
                return_type = parse_result.text(rt_nodes[0]).strip(": ")

            docstring = self._extract_docstring(def_node, parse_result)
            is_async = self._node_has_async(def_node, parse_result)
            complexity = self._cyclomatic_complexity(def_node)

            fn = FunctionNode(
                id=fn_id,
                file=rel,
                name=name,
                kind="function",
                language=profile.language,
                line_start=def_node.start_point[0] + 1,
                line_end=def_node.end_point[0] + 1,
                params=params,
                return_type=return_type,
                docstring=docstring,
                is_async=is_async,
                lines=def_node.end_point[0] - def_node.start_point[0] + 1,
                complexity=complexity,
            )
            graph.functions[fn_id] = fn
            fn_ids.append(fn_id)

        return fn_ids

    def _extract_params(self, param_nodes: list, parse_result: ParseResult) -> list[Param]:
        if not param_nodes:
            return []
        params_node = param_nodes[0]
        text = parse_result.text(params_node).strip("()")
        params: list[Param] = []
        for part in text.split(","):
            part = part.strip()
            if not part or part in ("self", "cls", "*", "**kwargs", "*args"):
                continue
            name, type_hint, default = part, None, None
            if "=" in name:
                name, default = name.split("=", 1)
                name = name.strip()
                default = default.strip()
            if ":" in name:
                name, type_hint = name.split(":", 1)
                name = name.strip()
                type_hint = type_hint.strip()
            if not _VALID_IDENT.match(name):
                continue
            params.append(Param(name=name, type_hint=type_hint, default=default))
        return params

    def _extract_routes(
        self, parse_result: ParseResult, rel: str, graph: Graph,
    ) -> None:
        """Find route decorators and annotate FunctionNodes."""
        profile = parse_result.profile
        if not profile.decorator_query:
            return

        # Route patterns: @app.route, @app.get, @router.post, @bp.put, etc.
        METHODS_MAP = {
            "route": "GET", "get": "GET", "post": "POST",
            "put": "PUT", "delete": "DELETE", "patch": "PATCH",
        }

        matches = self._parser.query_matches(parse_result, profile.decorator_query)

        # Collect all decorator args per function (query returns
        # separate matches for each argument in argument_list)
        fn_dec: dict[str, dict] = {}
        for _pattern_idx, capture_dict in matches:
            dec_name_nodes = capture_dict.get("dec.name", [])
            fn_name_nodes = capture_dict.get("dec.fn_name", [])
            dec_arg_nodes = capture_dict.get("dec.arg", [])

            if not dec_name_nodes or not fn_name_nodes:
                continue

            fn_name = parse_result.text(fn_name_nodes[0])
            if fn_name not in fn_dec:
                fn_dec[fn_name] = {
                    "dec_text": parse_result.text(dec_name_nodes[0]),
                    "args": [],
                }
            for node in dec_arg_nodes:
                fn_dec[fn_name]["args"].append(parse_result.text(node))

        for fn_name, info in fn_dec.items():
            fn_id = f"{rel}::{fn_name}"
            fn = graph.functions.get(fn_id)
            if fn is None:
                continue

            parts = info["dec_text"].rsplit(".", 1)
            method_name = parts[-1].lower() if parts else ""
            if method_name not in METHODS_MAP:
                continue

            # Find path: first arg starting with /
            route_path = ""
            for arg in info["args"]:
                raw = arg.strip("\"'")
                if raw.startswith("/"):
                    route_path = raw
                    break
            if not route_path:
                continue

            http_method = METHODS_MAP[method_name]
            if method_name == "route":
                for arg in info["args"]:
                    upper = arg.upper()
                    if "METHODS" in upper:
                        if "POST" in upper:
                            http_method = "POST"
                        elif "PUT" in upper:
                            http_method = "PUT"
                        elif "DELETE" in upper:
                            http_method = "DELETE"

            fn.route = route_path
            fn.http_method = http_method

    def _extract_docstring(self, fn_node, parse_result: ParseResult) -> str | None:
        for child in fn_node.children:
            if child.type == "block":
                for stmt in child.children:
                    if stmt.type == "expression_statement":
                        for inner in stmt.children:
                            if inner.type in ("string", "string_content"):
                                text = parse_result.text(inner)
                                return text.strip("\"'").split("\n")[0].strip()
        return None

    def _cyclomatic_complexity(self, node) -> int:
        """Count branching nodes: if/for/while/except/and/or/ternary."""
        BRANCH_TYPES = {
            "if_statement", "elif_clause", "for_statement", "while_statement",
            "except_clause", "with_statement",
            # Common across languages
            "if_expression", "conditional_expression", "ternary_expression",
            "for_in_statement", "catch_clause", "case_clause",
            "switch_case", "match_arm",
            # Boolean operators add paths
            "boolean_operator", "binary_expression",
        }
        BOOL_OPS = {"and", "or", "&&", "||"}

        count = 1  # base path
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in BRANCH_TYPES:
                # For binary_expression, only count if it's a boolean op
                if n.type == "binary_expression":
                    op_node = n.child_by_field_name("operator")
                    if op_node and op_node.type in BOOL_OPS:
                        count += 1
                else:
                    count += 1
            elif n.type == "boolean_operator":
                count += 1
            for child in n.children:
                stack.append(child)
        return count

    def _node_has_async(self, node, parse_result: ParseResult) -> bool:
        text = parse_result.text(node)
        return text.lstrip().startswith("async ")

    def _build_ui_map(self, graph: Graph, root: Path) -> None:
        """Scan HTML templates and link them to route handlers."""
        from winkers.ui_map import link_templates, scan_templates
        if not any(fn.route for fn in graph.functions.values()):
            return
        template_map = scan_templates(root)
        link_templates(graph, root, template_map)
