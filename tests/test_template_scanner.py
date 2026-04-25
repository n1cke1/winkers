"""Tests for winkers.templates.scanner."""

from pathlib import Path
from textwrap import dedent

from winkers.templates.scanner import (
    discover_templates,
    filter_leaves,
    scan_template,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

def test_finds_pane_sections(tmp_path):
    """Top-level <div class="tab-pane" id="..."> blocks are detected."""
    f = _write(tmp_path, "index.html", dedent("""
        <html>
        <body>
        <div id="pane-foo" class="tab-pane">
          <p>Foo content</p>
        </div>
        <div id="pane-bar" class="tab-pane active">
          <p>Bar content</p>
        </div>
        </body>
        </html>
    """).strip())
    sections = scan_template(f)
    ids = sorted(s.id for s in sections)
    assert ids == ["pane-bar", "pane-foo"]


def test_filter_leaves_excludes_parents(tmp_path):
    """Sections containing other sections are filtered out — leaves only."""
    f = _write(tmp_path, "x.html", dedent("""
        <div id="pane-calc" class="tab-pane">
          <div id="calc-sub-a" class="calc-subpane">A</div>
          <div id="calc-sub-b" class="calc-subpane">B</div>
        </div>
        <div id="pane-results" class="tab-pane">Results</div>
    """).strip())
    leaves = filter_leaves(scan_template(f))
    ids = sorted(s.id for s in leaves)
    # pane-calc has child sub-panes → filtered out; subpanes + standalone kept.
    assert ids == ["calc-sub-a", "calc-sub-b", "pane-results"]


def test_div_without_id_ignored(tmp_path):
    """Pane divs without id don't qualify as sections."""
    f = _write(tmp_path, "x.html", dedent("""
        <div class="tab-pane">no id, ignored</div>
        <div id="pane-real" class="tab-pane">tracked</div>
    """).strip())
    sections = scan_template(f)
    ids = [s.id for s in sections]
    assert ids == ["pane-real"]


def test_div_with_unrelated_class_ignored(tmp_path):
    """Random class names without pane/subpane markers don't count."""
    f = _write(tmp_path, "x.html", dedent("""
        <div id="card-1" class="card">not a section</div>
        <div id="pane-real" class="tab-pane">section</div>
    """).strip())
    sections = scan_template(f)
    assert [s.id for s in sections] == ["pane-real"]


def test_leading_comment_attached(tmp_path):
    """Comments immediately before a section are captured for naming hints."""
    f = _write(tmp_path, "x.html", dedent("""
        <!-- ── Tab: Approach ── -->
        <div id="pane-approach" class="tab-pane">
          content
        </div>
    """).strip())
    sections = scan_template(f)
    assert sections[0].leading_comment.strip().startswith("──")


def test_section_content_includes_open_and_close(tmp_path):
    """Captured content spans from opening tag through closing tag."""
    f = _write(tmp_path, "x.html", dedent("""
        <div id="pane-foo" class="tab-pane">
        <p>inner</p>
        </div>
    """).strip())
    sections = scan_template(f)
    assert "pane-foo" in sections[0].content
    assert "<p>inner</p>" in sections[0].content


def test_line_numbers_one_indexed(tmp_path):
    """start_line is 1-based and points at the opening tag."""
    f = _write(tmp_path, "x.html",
               "<html>\n<body>\n<div id=\"pane-x\" class=\"tab-pane\">a</div>\n</body></html>")
    sections = scan_template(f)
    assert sections[0].start_line == 3


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_malformed_html_doesnt_crash(tmp_path):
    """Unbalanced tags shouldn't raise — best-effort recovery."""
    f = _write(tmp_path, "broken.html",
               '<div id="x" class="tab-pane"><p>unclosed')
    # Should not raise.
    scan_template(f)


def test_empty_file_returns_empty_list(tmp_path):
    f = _write(tmp_path, "empty.html", "")
    assert scan_template(f) == []


# ---------------------------------------------------------------------------
# discover_templates
# ---------------------------------------------------------------------------

def test_discover_finds_html_jinja(tmp_path):
    (tmp_path / "templates").mkdir()
    _write(tmp_path / "templates", "a.html", "<div></div>")
    _write(tmp_path / "templates", "b.j2", "<span>{{ x }}</span>")
    _write(tmp_path / "templates", "c.txt", "ignored")
    found = sorted(p.name for p in discover_templates(tmp_path))
    assert found == ["a.html", "b.j2"]


def test_discover_skips_ignored_dirs(tmp_path):
    """node_modules / .venv / __pycache__ are skipped."""
    (tmp_path / "node_modules").mkdir()
    _write(tmp_path / "node_modules", "vendor.html", "<div></div>")
    _write(tmp_path, "real.html", "<div></div>")
    found = [p.name for p in discover_templates(tmp_path)]
    assert "real.html" in found
    assert "vendor.html" not in found
