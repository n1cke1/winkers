"""Template scanner: finds user-facing sections in HTML/Jinja2 templates.

A section is a `<div>` whose class contains a pane-like marker
(`tab-pane`, `subpane`, etc.) AND has an id. Both conditions are
required — generic divs and unidentified panes are skipped.

Filter to leaves (sections with no nested section descendants) since
parent panes like `pane-calc` are navigation wrappers, not content
units. The user-facing concepts always live at the leaf level.

Stdlib `html.parser` is used for robustness over comments/scripts;
no extra dependencies. Same approach as ui_map.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

# Class names that mark a "section" container. Conservative list — not every
# div with "pane" or "panel" is a structural section, so we also require id.
_PANE_CLASS_RE = re.compile(
    r"\b(tab-pane|subpane|tab-content|calc-subpane|content-pane)\b",
    re.IGNORECASE,
)

_TEMPLATE_EXTS = {".html", ".jinja2", ".j2", ".tpl"}
_IGNORE_DIRS = {"node_modules", ".venv", "venv", "__pycache__",
                ".git", "dist", "build"}


@dataclass
class TemplateSection:
    """One user-facing region of a template."""
    id: str                      # DOM id, e.g. "pane-results"
    file: str                    # relative path
    anchor: str                  # human-readable, e.g. '#pane-results'
    class_attr: str              # full class string at scan time
    start_line: int              # 1-based, opening tag
    end_line: int                # 1-based, closing tag
    content: str                 # raw markup including open/close tags
    has_subsections: bool = False
    # Comment markers nearby — useful for naming when id is generic.
    leading_comment: str = ""


class _SectionScanner(HTMLParser):
    """Walks the DOM, recording every <div> and noting which are sections.

    Tracks the open-div stack so we can pair start/end tags and detect
    parent-child relationships between sections.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        # Pre-compute line offsets to translate (line, col) → byte position.
        self._line_starts = [0]
        for ch in source:
            if ch == "\n":
                self._line_starts.append(len(self._line_starts))
        # Track recent comments so we can attach the latest to the next section.
        self._recent_comment: str = ""
        self._comment_line: int = 0
        self._div_stack: list[dict] = []
        self.sections: list[TemplateSection] = []

    # ------------------------------------------------------------------
    def handle_comment(self, data: str) -> None:
        text = data.strip()
        if 6 <= len(text) <= 200:
            self._recent_comment = text
            line, _ = self.getpos()
            self._comment_line = line

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() != "div":
            return
        attr = {k.lower(): v or "" for k, v in attrs}
        class_attr = attr.get("class", "")
        id_val = attr.get("id", "")
        is_section = bool(_PANE_CLASS_RE.search(class_attr) and id_val)
        line, _ = self.getpos()

        entry: dict = {
            "tag": "div",
            "id": id_val if is_section else None,
            "class": class_attr,
            "is_section": is_section,
            "start_line": line,
            "comment": "",
        }
        if is_section and self._recent_comment and line - self._comment_line <= 3:
            entry["comment"] = self._recent_comment

        if is_section:
            # Mark the nearest enclosing section as a parent.
            for parent in reversed(self._div_stack):
                if parent.get("is_section"):
                    parent["has_subsections"] = True
                    break

        self._div_stack.append(entry)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "div":
            return
        if not self._div_stack:
            return  # malformed input — best-effort, just skip
        # Pop the most recent <div>; HTML parser handles inline mismatches
        # by aligning the closing tag with the last open one.
        entry = self._div_stack.pop()
        if not entry.get("is_section"):
            return

        end_line, _ = self.getpos()
        content = self._extract_lines(entry["start_line"], end_line)
        self.sections.append(TemplateSection(
            id=entry["id"],
            file="",  # set by caller
            anchor=f"#{entry['id']}",
            class_attr=entry["class"],
            start_line=entry["start_line"],
            end_line=end_line,
            content=content,
            has_subsections=entry.get("has_subsections", False),
            leading_comment=entry.get("comment", ""),
        ))

    # ------------------------------------------------------------------
    def _extract_lines(self, start_line: int, end_line: int) -> str:
        """Return source between two 1-based line numbers, inclusive."""
        lines = self.source.splitlines(keepends=True)
        # Clamp to valid range.
        s = max(0, start_line - 1)
        e = min(len(lines), end_line + 1)  # +1 to include the closing line
        return "".join(lines[s:e])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_template(file: Path) -> list[TemplateSection]:
    """Scan a single template file. Returns ALL sections, including parents."""
    source = file.read_text(encoding="utf-8")
    scanner = _SectionScanner(source)
    scanner.feed(source)
    rel = str(file)
    for s in scanner.sections:
        s.file = rel
    return scanner.sections


def filter_leaves(sections: list[TemplateSection]) -> list[TemplateSection]:
    """Keep only sections without nested sections.

    For two-level structures (pane-calc → calc-sub-*), parent wrappers
    are navigation containers — the leaf sub-panes are what users
    interact with. Embedding the parent would dilute the index with
    overlapping text.
    """
    return [s for s in sections if not s.has_subsections]


def discover_templates(root: Path) -> list[Path]:
    """Recursively find template files under root, skipping ignored dirs."""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _TEMPLATE_EXTS:
            continue
        if any(part in _IGNORE_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def scan_project(root: Path) -> list[TemplateSection]:
    """One-call entry: find all leaf sections across all templates in root.

    Re-rewrites each section's `file` field to a path relative to `root`
    with forward slashes — portable across machines (VPS deploy) and
    Windows ↔ Linux. Without this, unit ids would contain absolute paths
    like `template:C:\\Development\\...\\index.html#approach`, breaking
    cross-machine copy.
    """
    sections: list[TemplateSection] = []
    for f in discover_templates(root):
        try:
            file_sections = scan_template(f)
        except Exception:
            # Don't fail the whole scan on one bad template.
            continue
        try:
            rel = str(f.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = f.name
        for s in file_sections:
            s.file = rel
        sections.extend(file_sections)
    return filter_leaves(sections)
