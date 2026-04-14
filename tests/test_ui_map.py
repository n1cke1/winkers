"""Tests for ui_map: template scanning and route→template linking."""

from pathlib import Path

from winkers.graph import GraphBuilder
from winkers.ui_map import scan_templates

FLASK_FIXTURE = Path(__file__).parent / "fixtures" / "flask_project"


def test_scan_templates_finds_all_files():
    result = scan_templates(FLASK_FIXTURE)
    keys = set(result.keys())
    assert "templates/index.html" in keys
    assert "templates/products/list.html" in keys


def test_scan_templates_index_elements():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    kinds = [e["kind"] for e in elements]
    assert "h1" in kinds
    assert "form" in kinds
    assert "panel" in kinds
    assert "tab" in kinds
    assert "button" in kinds
    assert "input" in kinds
    assert "select" in kinds
    assert "textarea" in kinds
    assert "indicator" in kinds


def test_scan_templates_index_h1_text():
    result = scan_templates(FLASK_FIXTURE)
    h1 = next(e for e in result["templates/index.html"] if e["kind"] == "h1")
    assert h1["text"] == "Dashboard"


def test_scan_templates_products_elements():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/products/list.html"]
    kinds = [e["kind"] for e in elements]
    assert "h2" in kinds
    assert "table" in kinds
    assert "form" in kinds
    assert "panel" in kinds  # modal matches panel pattern


def test_scan_templates_products_table_id():
    result = scan_templates(FLASK_FIXTURE)
    table = next(e for e in result["templates/products/list.html"] if e["kind"] == "table")
    assert table["id"] == "products-table"


def test_link_templates_sets_fn_template():
    graph = GraphBuilder().build(FLASK_FIXTURE)
    route_fns = {fn.route: fn for fn in graph.functions.values() if fn.route}
    assert route_fns["/"].template == "index.html"
    assert route_fns["/products"].template == "products/list.html"


def test_link_templates_about_has_no_template():
    graph = GraphBuilder().build(FLASK_FIXTURE)
    about = next(fn for fn in graph.functions.values() if fn.name == "about")
    assert about.template is None


def test_link_templates_populates_meta():
    graph = GraphBuilder().build(FLASK_FIXTURE)
    ui_map = graph.meta.get("ui_map", {})
    assert "/" in ui_map
    assert "/products" in ui_map
    assert "/about" not in ui_map


def test_ui_map_entry_structure():
    graph = GraphBuilder().build(FLASK_FIXTURE)
    entry = graph.meta["ui_map"]["/"]
    assert entry["handler"] == "index"
    assert entry["template"] == "index.html"
    assert isinstance(entry["elements"], list)
    assert any(e["kind"] == "h1" for e in entry["elements"])


def test_section_ui_map_via_orient(tmp_path):
    """orient(include=['ui_map']) returns expected structure."""
    from winkers.mcp.tools import _section_ui_map
    graph = GraphBuilder().build(FLASK_FIXTURE)
    result = _section_ui_map(graph, zone_filter=None)
    assert result["count"] >= 2
    assert "/" in result["routes"]


def test_scan_templates_tab_with_text():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    tabs = [e for e in elements if e["kind"] == "tab"]
    assert len(tabs) >= 1
    # data-tab tab should have text
    main_tab = next(e for e in tabs if e.get("data-tab") == "results")
    assert main_tab["text"] == "Результаты"


def test_scan_templates_subtab():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    tabs = [e for e in elements if e["kind"] == "tab"]
    subtab = next((e for e in tabs if "data-csub" in e), None)
    assert subtab is not None
    assert subtab["data-csub"] == "formulas"
    assert subtab["text"] == "Формулы"


def test_scan_templates_button():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    btn = next(e for e in elements if e["kind"] == "button")
    assert btn["id"] == "btn-calculate"
    assert btn["onclick"] == "runCalculate()"
    assert "Рассчитать" in btn["text"]


def test_scan_templates_input():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    inp = next(e for e in elements if e["kind"] == "input")
    assert inp["id"] == "inp-search"
    assert inp["type"] == "text"
    assert inp["placeholder"] == "Search..."


def test_scan_templates_select():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    sel = next(e for e in elements if e["kind"] == "select")
    assert sel["id"] == "inp-objective"
    assert sel["name"] == "objective"


def test_scan_templates_textarea():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    ta = next(e for e in elements if e["kind"] == "textarea")
    assert ta["id"] == "chat-input"
    assert ta["placeholder"] == "Type here..."


def test_scan_templates_indicator():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    ind = next(e for e in elements if e["kind"] == "indicator")
    assert ind["id"] == "server-status"


def test_panel_re_extended():
    result = scan_templates(FLASK_FIXTURE)
    elements = result["templates/index.html"]
    panels = [e for e in elements if e["kind"] == "panel"]
    panel_ids = [p["id"] for p in panels]
    assert "topbar" in panel_ids


def test_section_ui_map_empty_graph():
    from winkers.mcp.tools import _section_ui_map
    from winkers.models import Graph
    graph = Graph()
    result = _section_ui_map(graph, zone_filter=None)
    assert result["count"] == 0
    assert "note" in result
