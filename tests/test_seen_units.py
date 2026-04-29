"""Wave 7 — context dedup via SeenUnitsRegistry."""

from __future__ import annotations

import pytest

from winkers.session.seen_units import (
    DEFAULT_THRESHOLD,
    SeenUnitsRegistry,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Clean slate before each test — singleton state would otherwise leak."""
    reset_for_tests()
    yield
    reset_for_tests()


# ---------------------------------------------------------------------------
# Core registry
# ---------------------------------------------------------------------------


class TestRegistryBasics:
    def test_singleton_returns_same_instance(self):
        a = SeenUnitsRegistry.get()
        b = SeenUnitsRegistry.get()
        assert a is b

    def test_reset_drops_singleton(self):
        a = SeenUnitsRegistry.get()
        reset_for_tests()
        b = SeenUnitsRegistry.get()
        assert a is not b

    def test_unrecorded_unit_not_seen(self):
        reg = SeenUnitsRegistry.get()
        assert reg.is_recently_seen("u1") is False
        assert reg.recent_marker("u1") is None

    def test_record_then_seen(self):
        reg = SeenUnitsRegistry.get()
        idx = reg.begin_call("find_work_area")
        reg.record(["u1"], "find_work_area", idx)
        assert reg.is_recently_seen("u1")
        assert reg.recent_marker("u1") == f"find_work_area@call#{idx}"

    def test_marker_format(self):
        reg = SeenUnitsRegistry.get()
        # Burn a few call_idx values so we test a non-trivial number
        reg.begin_call("orient")
        reg.begin_call("orient")
        idx = reg.begin_call("scope")
        reg.record(["target"], "scope", idx)
        assert reg.recent_marker("target") == f"scope@call#{idx}"

    def test_record_empty_no_op(self):
        reg = SeenUnitsRegistry.get()
        idx = reg.begin_call("find_work_area")
        reg.record([], "find_work_area", idx)
        assert reg.recent_marker("u1") is None


# ---------------------------------------------------------------------------
# Threshold expiration
# ---------------------------------------------------------------------------


class TestThreshold:
    def test_default_threshold_is_ten(self):
        assert DEFAULT_THRESHOLD == 10

    def test_seen_within_threshold(self):
        reg = SeenUnitsRegistry.get()
        idx = reg.begin_call("find_work_area")
        reg.record(["u1"], "find_work_area", idx)
        # Burn a few unrelated calls
        for _ in range(5):
            reg.begin_call("other")
        assert reg.is_recently_seen("u1")  # gap=5 < 10

    def test_expires_past_threshold(self):
        reg = SeenUnitsRegistry.get()
        idx = reg.begin_call("find_work_area")
        reg.record(["u1"], "find_work_area", idx)
        # Burn `threshold` more calls so the entry ages out
        for _ in range(DEFAULT_THRESHOLD):
            reg.begin_call("noise")
        assert reg.is_recently_seen("u1") is False
        assert reg.recent_marker("u1") is None

    def test_custom_threshold_via_constructor(self):
        reg = SeenUnitsRegistry(threshold=3)
        idx = reg.begin_call("orient")
        reg.record(["u1"], "orient", idx)
        reg.begin_call("orient")
        reg.begin_call("orient")
        # gap=3 → should be expired (>= threshold)
        assert reg.is_recently_seen("u1") is False


# ---------------------------------------------------------------------------
# Suppression behaviour
# ---------------------------------------------------------------------------


class TestSuppression:
    def test_suppress_swaps_description_for_marker(self):
        reg = SeenUnitsRegistry.get()
        idx = reg.begin_call("find_work_area")
        reg.record(["u1"], "find_work_area", idx)
        item = {
            "id": "u1",
            "name": "do_thing",
            "summary": "short",
            "description": "long paragraph...",
        }
        reg.maybe_suppress_description("u1", item)
        assert "description" not in item
        assert item["description_seen_in"] == f"find_work_area@call#{idx}"
        # Other fields untouched.
        assert item["summary"] == "short"
        assert item["name"] == "do_thing"

    def test_no_suppress_for_unseen(self):
        reg = SeenUnitsRegistry.get()
        item = {"id": "u1", "description": "long paragraph..."}
        reg.maybe_suppress_description("u1", item)
        assert item["description"] == "long paragraph..."
        assert "description_seen_in" not in item

    def test_no_suppress_after_threshold(self):
        reg = SeenUnitsRegistry(threshold=3)
        idx = reg.begin_call("find_work_area")
        reg.record(["u1"], "find_work_area", idx)
        # Age the entry out
        for _ in range(3):
            reg.begin_call("noise")
        item = {"id": "u1", "description": "long paragraph..."}
        reg.maybe_suppress_description("u1", item)
        # Description survives — entry has expired.
        assert item["description"] == "long paragraph..."
        assert "description_seen_in" not in item

    def test_re_record_resets_window(self):
        reg = SeenUnitsRegistry(threshold=3)
        # First show
        idx1 = reg.begin_call("find_work_area")
        reg.record(["u1"], "find_work_area", idx1)
        # Age out
        for _ in range(3):
            reg.begin_call("noise")
        assert reg.recent_marker("u1") is None
        # Re-show
        idx2 = reg.begin_call("orient")
        reg.record(["u1"], "orient", idx2)
        assert reg.recent_marker("u1") == f"orient@call#{idx2}"
