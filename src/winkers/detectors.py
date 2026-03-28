"""Tree-sitter based rule detectors — run during winkers init."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from winkers.conventions import ProposedRule
from winkers.parser import TreeSitterParser


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if ".venv" not in p.parts]


def _parse(path: Path, parser: TreeSitterParser):
    from winkers.languages.python import PythonProfile
    try:
        return parser.parse_file(path, PythonProfile())
    except Exception:
        return None


def run_all_detectors(root: Path) -> list[ProposedRule]:
    """Run all detectors and return proposed rules."""
    rules: list[ProposedRule] = []
    for cls in (
        BaseClassDetector,
        ErrorHandlerDetector,
        DecoratorDetector,
        TestFixtureDetector,
        ImportPatternDetector,
    ):
        try:
            rules.extend(cls().detect(root))
        except Exception:
            pass
    return rules


# ---------------------------------------------------------------------------
# 1. BaseClassDetector
# ---------------------------------------------------------------------------

class BaseClassDetector:
    """Propose architecture rule when a class is inherited by 3+ models."""

    _SKIP = {"object", "Exception", "BaseException", "ABC", "Enum"}

    _QUERY = """
    (class_definition
      superclasses: (argument_list
        (identifier) @base.name))
    """

    def detect(self, root: Path) -> list[ProposedRule]:
        parser = TreeSitterParser()
        base_counts: dict[str, list[str]] = defaultdict(list)

        for path in _python_files(root):
            result = _parse(path, parser)
            if result is None:
                continue
            caps = parser.query_captures(result, self._QUERY)
            for node in caps.get("base.name", []):
                name = result.text(node)
                if name not in self._SKIP:
                    rel = str(path.relative_to(root))
                    base_counts[name].append(rel)

        rules = []
        for base, files in base_counts.items():
            unique = sorted(set(files))
            if len(unique) >= 3:
                rules.append(ProposedRule(
                    category="architecture",
                    title=f"{base} inheritance",
                    content=(
                        f"All models inherit from {base} "
                        f"({len(unique)} files follow this pattern)"
                    ),
                    affects=unique,
                    related=["data"],
                ))
        return rules


# ---------------------------------------------------------------------------
# 2. ErrorHandlerDetector
# ---------------------------------------------------------------------------

class ErrorHandlerDetector:
    """Propose errors rule when a function is the central error handler."""

    _QUERY = """
    (function_definition
      name: (identifier) @fn.name
      body: (block
        (return_statement
          (expression_list) @ret.tuple)))
    """

    def detect(self, root: Path) -> list[ProposedRule]:
        parser = TreeSitterParser()
        handlers: list[tuple[str, str]] = []  # (fn_name, rel_path)

        for path in _python_files(root):
            result = _parse(path, parser)
            if result is None:
                continue
            for _, match in parser.query_matches(result, self._QUERY):
                fn_nodes = match.get("fn.name", [])
                if fn_nodes:
                    fn_name = result.text(fn_nodes[0])
                    rel = str(path.relative_to(root)).replace("\\", "/")
                    handlers.append((fn_name, rel))

        if not handlers:
            return []

        # Group by file — if a file has 2+ error handlers it's the error module
        file_counts: dict[str, list[str]] = defaultdict(list)
        for fn_name, rel in handlers:
            file_counts[rel].append(fn_name)

        rules = []
        for rel, fns in file_counts.items():
            threshold = 1 if "error" in rel.lower() else 2
            if len(fns) >= threshold:
                rules.append(ProposedRule(
                    category="errors",
                    title="Centralised error responses",
                    content=f"Return errors via functions in {rel} ({', '.join(fns[:3])})",
                    affects=[rel],
                    related=["api"],
                ))
        return rules


# ---------------------------------------------------------------------------
# 3. DecoratorDetector
# ---------------------------------------------------------------------------

class DecoratorDetector:
    """Propose validation/auth rule when a decorator appears on 3+ functions."""

    _SKIP = {"property", "staticmethod", "classmethod", "abstractmethod",
              "override", "pytest", "fixture"}

    _QUERY = """
    (decorated_definition
      (decorator
        [(identifier) @dec.name
         (call function: (identifier) @dec.name)
         (call function: (attribute attribute: (identifier) @dec.name))
         (attribute attribute: (identifier) @dec.name)])
      definition: (function_definition name: (identifier) @fn.name))
    """

    def detect(self, root: Path) -> list[ProposedRule]:
        parser = TreeSitterParser()
        dec_files: dict[str, set[str]] = defaultdict(set)

        for path in _python_files(root):
            result = _parse(path, parser)
            if result is None:
                continue
            for _, match in parser.query_matches(result, self._QUERY):
                dec_nodes = match.get("dec.name", [])
                if not dec_nodes:
                    continue
                dec_name = result.text(dec_nodes[0])
                if dec_name in self._SKIP:
                    continue
                rel = str(path.relative_to(root))
                dec_files[dec_name].add(rel)

        rules = []
        for dec_name, files in dec_files.items():
            if len(files) >= 2:
                rules.append(ProposedRule(
                    category="validation",
                    title=f"@{dec_name} decorator",
                    content=(
                        f"Use @{dec_name} decorator on functions that require it "
                        f"({len(files)} files)"
                    ),
                    affects=sorted(files),
                    related=["api", "errors"],
                ))
        return rules


# ---------------------------------------------------------------------------
# 4. TestFixtureDetector
# ---------------------------------------------------------------------------

class TestFixtureDetector:
    """Propose testing rule from pytest fixtures in conftest.py."""

    _QUERY = """
    (decorated_definition
      (decorator
        [(identifier) @dec
         (attribute attribute: (identifier) @dec)])
      definition: (function_definition name: (identifier) @fixture.name))
    """

    def detect(self, root: Path) -> list[ProposedRule]:
        parser = TreeSitterParser()
        fixtures: list[tuple[str, str]] = []  # (fixture_name, conftest_path)

        for path in root.rglob("conftest.py"):
            if ".venv" in path.parts:
                continue
            result = _parse(path, parser)
            if result is None:
                continue
            for _, match in parser.query_matches(result, self._QUERY):
                dec_nodes = match.get("dec", [])
                fix_nodes = match.get("fixture.name", [])
                if not dec_nodes or not fix_nodes:
                    continue
                dec_name = result.text(dec_nodes[0])
                if dec_name in ("fixture",):
                    rel = str(path.relative_to(root))
                    fixtures.append((result.text(fix_nodes[0]), rel))

        if len(fixtures) < 2:
            return []

        names = [f[0] for f in fixtures]
        conftest = fixtures[0][1]
        return [ProposedRule(
            category="testing",
            title="Pytest fixtures",
            content=(
                f"Use shared fixtures from {conftest}: "
                f"{', '.join(names[:5])}"
            ),
            affects=[conftest],
            related=["validation"],
        )]


# ---------------------------------------------------------------------------
# 5. ImportPatternDetector
# ---------------------------------------------------------------------------

class ImportPatternDetector:
    """Propose rule when a project-internal utility module is imported by 3+ files."""

    _QUERY = """
    (import_from_statement
      module_name: (dotted_name) @imp.module)
    """

    def detect(self, root: Path) -> list[ProposedRule]:
        parser = TreeSitterParser()
        # Map module path → list of importing files
        module_importers: dict[str, list[str]] = defaultdict(list)
        all_py = _python_files(root)

        # Build set of project module paths for lookup
        project_modules = {
            str(p.relative_to(root)).replace("\\", "/").replace(".py", "").replace("/", ".")
            for p in all_py
        }

        for path in all_py:
            result = _parse(path, parser)
            if result is None:
                continue
            caps = parser.query_captures(result, self._QUERY)
            for node in caps.get("imp.module", []):
                module = result.text(node)
                if module in project_modules:
                    rel = str(path.relative_to(root))
                    module_importers[module].append(rel)

        rules = []
        for module, importers in module_importers.items():
            unique = sorted(set(importers))
            if len(unique) >= 3:
                # Convert dotted module to file path
                module_file = module.replace(".", "/") + ".py"
                rules.append(ProposedRule(
                    category="architecture",
                    title=f"{module} utility",
                    content=(
                        f"Import from {module_file} instead of reimplementing "
                        f"({len(unique)} files already use it)"
                    ),
                    affects=unique,
                    related=["validation"],
                ))
        return rules
