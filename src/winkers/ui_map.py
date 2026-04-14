"""UI map: links Flask routes to Jinja2 templates and extracts UI elements."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from winkers.models import Graph

_TEMPLATE_EXTS = {".html", ".jinja2", ".j2"}
_IGNORE = {"node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build"}
_RENDER_RE = re.compile(r'render_template\(\s*["\']([^"\']+)["\']')
_PANEL_RE = re.compile(
    r"panel|modal|section|card|pane|wrap|container|toolbar|strip|bar|overlay|toast|loading",
    re.IGNORECASE,
)
_TAB_ATTR_RE = re.compile(r"^data-.*(?:tab|sub)")
_TAB_CLASS_RE = re.compile(r"tab|subtab", re.IGNORECASE)


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict] = []
        self._capture_text: str | None = None  # tag name being captured
        self._pending: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        id_val = attr.get("id", "") or ""
        class_val = attr.get("class", "") or ""

        if tag in ("h1", "h2"):
            self._capture_text = tag
            self._pending = {"kind": tag, "text": ""}

        elif tag == "table":
            self.elements.append({"kind": "table", "id": id_val})

        elif tag == "form":
            self.elements.append({
                "kind": "form",
                "action": attr.get("action", ""),
                "method": (attr.get("method") or "GET").upper(),
                "id": id_val,
            })

        elif tag == "button":
            entry: dict = {"kind": "button", "id": id_val}
            if attr.get("onclick"):
                entry["onclick"] = attr["onclick"]
            # Capture text content
            self._capture_text = "button"
            self._pending = {**entry, "text": ""}

        elif tag == "input":
            self.elements.append({
                "kind": "input",
                "id": id_val,
                "type": attr.get("type", "text"),
                "name": attr.get("name", ""),
                "placeholder": attr.get("placeholder", ""),
            })

        elif tag == "select":
            self.elements.append({
                "kind": "select",
                "id": id_val,
                "name": attr.get("name", ""),
            })

        elif tag == "textarea":
            self.elements.append({
                "kind": "textarea",
                "id": id_val,
                "name": attr.get("name", ""),
                "placeholder": attr.get("placeholder", ""),
            })

        elif tag in ("div", "li", "a"):
            # Tab / sub-tab detection: data-tab, data-*sub*, data-*tab*
            tab_attr = next(
                (k for k in attr if _TAB_ATTR_RE.match(k)), None,
            )
            if tab_attr is not None:
                self._capture_text = tag
                self._pending = {
                    "kind": "tab",
                    tab_attr: attr[tab_attr],
                    "id": id_val,
                    "text": "",
                }
            elif tag == "div" and (
                _PANEL_RE.search(id_val) or _PANEL_RE.search(class_val)
            ):
                self.elements.append({
                    "kind": "panel",
                    "id": id_val,
                    "class": class_val,
                })

        elif tag == "span" and id_val:
            self.elements.append({"kind": "indicator", "id": id_val})

    def handle_data(self, data: str) -> None:
        if self._pending is not None:
            self._pending["text"] += data.strip()

    def handle_endtag(self, tag: str) -> None:
        if self._pending is not None and tag == self._capture_text:
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


def _find_elements(
    tpl_name: str, source_file: str, template_map: dict[str, list[dict]],
) -> list[dict]:
    """Find template elements by searching for the closest match to tpl_name."""
    # 1. Exact match
    if tpl_name in template_map:
        return template_map[tpl_name]
    # 2. With "templates/" prefix
    if f"templates/{tpl_name}" in template_map:
        return template_map[f"templates/{tpl_name}"]
    # 3. Find all keys ending with the template name, pick closest to source file
    suffix = f"/{tpl_name}"
    candidates = [k for k in template_map if k.endswith(suffix)]
    if not candidates:
        return []
    if len(candidates) == 1:
        return template_map[candidates[0]]
    # Pick the candidate sharing the longest common prefix with source_file
    source_dir = source_file.rsplit("/", 1)[0] if "/" in source_file else ""
    best = max(candidates, key=lambda k: len(_common_prefix(k, source_dir)))
    return template_map[best]


def _common_prefix(a: str, b: str) -> str:
    result = []
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        result.append(ca)
    return "".join(result)


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

        elements = _find_elements(tpl_name, fn.file, template_map)

        ui_map[fn.route] = {
            "handler": fn.name,
            "file": fn.file,
            "template": tpl_name,
            "elements": elements,
        }

    if ui_map:
        graph.meta["ui_map"] = ui_map
