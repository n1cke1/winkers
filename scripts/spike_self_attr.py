"""Spike: measure static resolvability of `self.X.method()` call sites.

Goal: before investing in a heuristic resolver (Winkers Task 2), learn what
fraction of real `self.X.method(...)` calls in a target codebase can be
resolved via the proposed in-scope pattern (`self.X = ClassName(...)` in
__init__), and what fraction lives in out-of-scope patterns (DI via
constructor params, factories, chained self-attrs, etc.).

Usage:
    python scripts/spike_self_attr.py <path-to-project-root>

Skips .venv / migrations / tests / __pycache__.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from pathlib import Path


class Cat:
    CTOR_CALL = "ctor_call"          # self.x = Foo(...) or self.x = mod.Foo(...)
    DI_PARAM = "di_param"            # self.x = arg_of_init
    DI_SELF_ATTR = "di_self_attr"    # self.x = self.y.z  (chained)
    FACTORY = "factory"              # self.x = get_foo()
    LITERAL = "literal"              # self.x = [] / {} / 0 / None
    OTHER = "other"
    UNKNOWN_ATTR = "unknown_attr"    # call self.X.method but X not assigned in __init__


def _classify_rhs(rhs: ast.AST, init_params: set[str]) -> str:
    if isinstance(rhs, ast.Call):
        func = rhs.func
        if isinstance(func, ast.Name):
            return Cat.CTOR_CALL if func.id and func.id[0].isupper() else Cat.FACTORY
        if isinstance(func, ast.Attribute):
            return Cat.CTOR_CALL if func.attr and func.attr[0].isupper() else Cat.FACTORY
        return Cat.OTHER
    if isinstance(rhs, ast.Name):
        return Cat.DI_PARAM if rhs.id in init_params else Cat.OTHER
    if isinstance(rhs, ast.Attribute):
        return Cat.DI_SELF_ATTR
    if isinstance(rhs, (ast.List, ast.Dict, ast.Set, ast.Constant, ast.Tuple)):
        return Cat.LITERAL
    return Cat.OTHER


def _collect_init_attrs(cls: ast.ClassDef) -> dict[str, str]:
    """Return {attr: category} for `self.X = ...` statements in __init__."""
    out: dict[str, str] = {}
    init = next(
        (n for n in cls.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "__init__"),
        None,
    )
    if init is None:
        return out
    params = {a.arg for a in init.args.args if a.arg != "self"}
    params |= {a.arg for a in init.args.kwonlyargs}
    for node in ast.walk(init):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                # Last assignment wins (a real pass would diff-track; ok for spike)
                out[target.attr] = _classify_rhs(node.value, params)
    return out


def _find_self_attr_calls(cls: ast.ClassDef) -> list[tuple[str, str, int]]:
    """Return list of (attr, method, lineno) for `self.X.method(...)` in methods."""
    calls: list[tuple[str, str, int]] = []
    for method in cls.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if method.name == "__init__":
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # self.X.method(...)
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "self"):
                calls.append((func.value.attr, func.attr, node.lineno))
    return calls


SKIP_PARTS = {".venv", "venv", "migrations", "__pycache__", "tests",
              "node_modules", "dist", "build"}


def analyze(root: Path) -> None:
    per_call: Counter[str] = Counter()
    per_attr: Counter[str] = Counter()
    total_calls = 0
    total_classes = 0
    classes_with_init = 0
    samples: dict[str, list[str]] = {}

    for py in root.rglob("*.py"):
        if any(p in SKIP_PARTS for p in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            total_classes += 1
            attrs = _collect_init_attrs(node)
            if attrs:
                classes_with_init += 1
            for cat in attrs.values():
                per_attr[cat] += 1

            for attr, method, lineno in _find_self_attr_calls(node):
                total_calls += 1
                cat = attrs.get(attr, Cat.UNKNOWN_ATTR)
                per_call[cat] += 1
                bucket = samples.setdefault(cat, [])
                if len(bucket) < 3:
                    rel = py.relative_to(root).as_posix()
                    bucket.append(f"{rel}:{lineno}  self.{attr}.{method}(...)")

    print(f"Root:                     {root}")
    print(f"Classes scanned:          {total_classes}")
    print(f"Classes with __init__:    {classes_with_init}")
    print(f"self.X.method() sites:    {total_calls}")
    print()
    print("Call-site breakdown  —  what fraction the heuristic would resolve:")
    for cat in [Cat.CTOR_CALL, Cat.DI_PARAM, Cat.DI_SELF_ATTR, Cat.FACTORY,
                Cat.LITERAL, Cat.OTHER, Cat.UNKNOWN_ATTR]:
        n = per_call.get(cat, 0)
        pct = n * 100 / total_calls if total_calls else 0.0
        bar = "#" * int(pct / 2)
        print(f"  {cat:<14} {n:>5}  {pct:>5.1f}%  {bar}")

    total_attrs = sum(per_attr.values())
    print()
    print(f"Assignment-pattern breakdown in __init__  (attrs = {total_attrs}):")
    for cat in [Cat.CTOR_CALL, Cat.DI_PARAM, Cat.DI_SELF_ATTR, Cat.FACTORY,
                Cat.LITERAL, Cat.OTHER]:
        n = per_attr.get(cat, 0)
        pct = n * 100 / total_attrs if total_attrs else 0.0
        print(f"  {cat:<14} {n:>5}  {pct:>5.1f}%")

    print()
    print("Samples (up to 3 per category):")
    for cat in sorted(samples):
        print(f"  [{cat}]")
        for s in samples[cat]:
            print(f"    {s}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path, help="Project root to scan")
    args = p.parse_args()
    analyze(args.root.resolve())


if __name__ == "__main__":
    main()
