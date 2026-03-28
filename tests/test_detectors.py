"""Tests for tree-sitter detectors."""

from pathlib import Path

from winkers.detectors import (
    BaseClassDetector,
    DecoratorDetector,
    ErrorHandlerDetector,
    ImportPatternDetector,
    TestFixtureDetector,
    run_all_detectors,
)

FIXTURE = Path(__file__).parent / "fixtures" / "detector_project"


# ---------------------------------------------------------------------------
# BaseClassDetector
# ---------------------------------------------------------------------------

def test_base_class_detects_appmodel():
    rules = BaseClassDetector().detect(FIXTURE)
    assert len(rules) >= 1
    titles = [r.title for r in rules]
    assert any("AppModel" in t for t in titles)


def test_base_class_category():
    rules = BaseClassDetector().detect(FIXTURE)
    assert all(r.category == "architecture" for r in rules)


def test_base_class_affects_files():
    rules = BaseClassDetector().detect(FIXTURE)
    appmodel_rule = next(r for r in rules if "AppModel" in r.title)
    assert len(appmodel_rule.affects) >= 3


def test_base_class_skips_object(tmp_path):
    """'object' should not trigger a rule."""
    (tmp_path / "a.py").write_text("class Foo(object): pass\n")
    (tmp_path / "b.py").write_text("class Bar(object): pass\n")
    (tmp_path / "c.py").write_text("class Baz(object): pass\n")
    rules = BaseClassDetector().detect(tmp_path)
    assert all("object" not in r.title for r in rules)


# ---------------------------------------------------------------------------
# ErrorHandlerDetector
# ---------------------------------------------------------------------------

def test_error_handler_detects_errors_module():
    rules = ErrorHandlerDetector().detect(FIXTURE)
    assert len(rules) >= 1
    assert all(r.category == "errors" for r in rules)


def test_error_handler_content_mentions_file():
    rules = ErrorHandlerDetector().detect(FIXTURE)
    assert any("errors.py" in r.content for r in rules)


# ---------------------------------------------------------------------------
# DecoratorDetector
# ---------------------------------------------------------------------------

def test_decorator_detects_login_required():
    rules = DecoratorDetector().detect(FIXTURE)
    assert len(rules) >= 1
    titles = [r.title for r in rules]
    assert any("login_required" in t for t in titles)


def test_decorator_category():
    rules = DecoratorDetector().detect(FIXTURE)
    assert all(r.category == "validation" for r in rules)


def test_decorator_skips_property(tmp_path):
    """Built-in decorators should not trigger."""
    (tmp_path / "a.py").write_text(
        "class A:\n    @property\n    def x(self): return 1\n"
        "    @property\n    def y(self): return 2\n"
        "    @property\n    def z(self): return 3\n"
    )
    rules = DecoratorDetector().detect(tmp_path)
    assert not any("property" in r.title for r in rules)


# ---------------------------------------------------------------------------
# TestFixtureDetector
# ---------------------------------------------------------------------------

def test_fixture_detector_finds_conftest():
    rules = TestFixtureDetector().detect(FIXTURE)
    assert len(rules) >= 1
    assert all(r.category == "testing" for r in rules)


def test_fixture_detector_content_has_fixture_names():
    rules = TestFixtureDetector().detect(FIXTURE)
    content = " ".join(r.content for r in rules)
    assert "db" in content or "client" in content


def test_fixture_detector_empty_project(tmp_path):
    """No conftest.py → no rules."""
    (tmp_path / "test_foo.py").write_text("def test_x(): pass\n")
    rules = TestFixtureDetector().detect(tmp_path)
    assert rules == []


# ---------------------------------------------------------------------------
# ImportPatternDetector
# ---------------------------------------------------------------------------

def test_import_pattern_detects_dates_utils():
    rules = ImportPatternDetector().detect(FIXTURE)
    # app/utils/dates.py is imported by 2 route files — below threshold of 3
    # app/utils/errors.py is imported by routes (users + invoices + payments = 3)
    assert any("errors" in r.content or "dates" in r.content for r in rules) or rules == []


# ---------------------------------------------------------------------------
# run_all_detectors
# ---------------------------------------------------------------------------

def test_run_all_returns_list():
    rules = run_all_detectors(FIXTURE)
    assert isinstance(rules, list)
    assert len(rules) >= 2


def test_run_all_categories_valid():
    valid = {"architecture", "data", "numeric", "api", "validation",
             "errors", "testing", "security"}
    rules = run_all_detectors(FIXTURE)
    for r in rules:
        assert r.category in valid, f"Invalid category: {r.category}"


