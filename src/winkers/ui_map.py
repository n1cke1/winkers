"""UI map: links Flask routes to Jinja2 templates and extracts UI elements."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from winkers.models import Graph

_TEMPLATE_EXTS = {".html", ".jinja2", ".j2"}
_IGNORE = {"node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build"}
_RENDER_RE = re.compile(r'render_template\(\s*["\']([^"\']+)["\']')
_PANEL_RE = re.compile(r"panel|modal|section|card|pane|wrap|container", re.IGNORECASE)


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict] = []
        self._capture_text: str | None = None  # "h1" | "h2"
        self._pending: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag in ("h1", "h2"):
            self._capture_text = tag
            self._pending = {"kind": tag, "text": ""}
        elif tag == "table":
            self.elements.append({"kind": "table", "id": attr.get("id", "")})
        elif tag == "form":
            self.elements.append({
                "kind": "form",
                "action": attr.get("action", ""),
                "method": (attr.get("method") or "GET").upper(),
                "id": attr.get("id", ""),
            })
        elif tag == "div":
            id_val = attr.get("id", "") or ""
            class_val = attr.get("class", "") or ""
            if "data-tab" in attr:
                self.elements.append({
                    "kind": "tab",
                    "data-tab": attr["data-tab"],
                    "id": id_val,
                })
            elif _PANEL_RE.search(id_val) or _PANEL_RE.search(class_val):
                self.elements.append({
                    "kind": "panel",
                    "id": id_val,
                    "class": class_val,
                })

    def handle_data(self, data: str) -> None:
        if self._pending is not None:
            self._pending["text"] += data.strip()

    def handle_endtag(self, tag: str) -> None:
        if self._pending is not None and tag == self._capture_text:
            if self._pending["text"]:
                self.elements.append(self._pending)
            self._pending = None
            self._capture_text = None


def scan_templates(root: Path) -> dict[str, list[dict]]:
    """Walk project for template files and extract UI elements.

    Returns dict keyed by relative template path (e.g. "templates/index.html").
    """
    result: dict[str, list[dict]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _IGNORE for part in path.parts):
            continue
        if path.suffix not in _TEMPLATE_EXTS:
            continue
        rel = path.relative_to(root).as_posix()
        collector = _ElementCollector()
        try:
            collector.feed(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        result[rel] = collector.elements
    return result


def link_templates(graph: Graph, root: Path, template_map: dict[str, list[dict]]) -> None:
    """Link route handlers to templates and store ui_map in graph.meta."""
    ui_map: dict[str, dict] = {}

    for fn in graph.functions.values():
        if not fn.route:
            continue
        try:
            source_path = root / fn.file
            lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
            fn_lines = "\n".join(lines[fn.line_start - 1: fn.line_end])
        except OSError:
            continue

        m = _RENDER_RE.search(fn_lines)
        if not m:
            continue

        tpl_name = m.group(1)
        fn.template = tpl_name

        # Find elements: try exact match, then with "templates/" prefix
        elements = template_map.get(tpl_name) or template_map.get(f"templates/{tpl_name}", [])

        ui_map[fn.route] = {
            "handler": fn.name,
            "file": fn.file,
            "template": tpl_name,
            "elements": elements,
        }

    if ui_map:
        graph.meta["ui_map"] = ui_map
