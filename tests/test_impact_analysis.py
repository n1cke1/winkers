"""Tests for winkers.impact — models, prompt/parse, store, tool integration.

Does not hit the LLM. Real-world generation is tested by --impact-only
dry-runs in dev; here we only verify the plumbing around it.
"""

import json
from pathlib import Path

import pytest

from winkers.graph import GraphBuilder
from winkers.impact.models import (
    CallerClassification,
    FunctionContext,
    ImpactFile,
    ImpactMeta,
    ImpactReport,
)
from winkers.impact.prompt import build_prompt, parse_response
from winkers.impact.store import ImpactStore
from winkers.mcp.tools import (
    _before_create_change,
    _section_hotspots,
    _tool_before_create,
    _tool_scope,
)
from winkers.models import FunctionNode, Graph
from winkers.resolver import CrossFileResolver
from winkers.target_resolution import ResolvedTargets

PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_project"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _report(risk="high", score=0.8, **overrides) -> ImpactReport:
    base = dict(
        content_hash="abc",
        risk_level=risk,
        risk_score=score,
        summary="does a thing",
        caller_classifications=[],
        safe_operations=["rename"],
        dangerous_operations=["change return type"],
        action_plan="step 1, step 2.",
    )
    base.update(overrides)
    return ImpactReport(**base)


def _write_impact(root: Path, fn_id: str, report: ImpactReport) -> None:
    store = ImpactStore(root)
    impact = ImpactFile(functions={fn_id: report}, meta=ImpactMeta(llm_model="test"))
    store.save(impact)


@pytest.fixture(scope="module")
def graph():
    g = GraphBuilder().build(PYTHON_FIXTURE)
    CrossFileResolver().resolve(g, str(PYTHON_FIXTURE))
    return g


# ---------------------------------------------------------------------------
# Prompt / parser
# ---------------------------------------------------------------------------

class TestPromptParser:
    def test_parses_valid_response(self):
        payload = {
            "primary_intent": "registers a new user",
            "secondary_intents": ["email validation", "password hashing"],
            "risk_level": "high",
            "risk_score": 0.85,
            "summary": "Creates user, hashes password, sends verification.",
            "caller_classifications": [
                {
                    "caller": "app/api.py::signup",
                    "dependency_type": "core_logic",
                    "coupling": "tight",
                    "update_effort": "moderate",
                    "note": "Uses return value directly",
                }
            ],
            "safe_operations": ["add optional parameter"],
            "dangerous_operations": ["change return type"],
            "action_plan": "Update signup caller first.",
        }
        result = parse_response(json.dumps(payload))
        assert result is not None
        assert result.primary_intent == "registers a new user"
        assert "email validation" in result.secondary_intents
        assert result.risk_level == "high"
        assert result.risk_score == pytest.approx(0.85)
        assert len(result.caller_classifications) == 1
        assert result.caller_classifications[0].coupling == "tight"

    def test_preserves_full_operation_descriptions(self):
        """Issue #1: dangerous_operations / safe_operations must keep the full
        LLM description, not a 15-char prefix. Benchmark showed fragments like
        'remove flush() ' and 'change return t' which are unusable for agents."""
        payload = {
            "primary_intent": "persists invoice",
            "secondary_intents": [],
            "risk_level": "medium",
            "risk_score": 0.5,
            "summary": "Writes invoice to DB.",
            "caller_classifications": [],
            "safe_operations": [
                "add an optional parameter with a sensible default value",
            ],
            "dangerous_operations": [
                "change return type from tuple to dataclass — breaks callers",
            ],
            "action_plan": "",
        }
        result = parse_response(json.dumps(payload))
        assert result is not None
        assert result.safe_operations == [
            "add an optional parameter with a sensible default value",
        ]
        assert result.dangerous_operations == [
            "change return type from tuple to dataclass — breaks callers",
        ]

    def test_parses_response_with_markdown_fences(self):
        raw = (
            '```json\n{"primary_intent":"x","risk_level":"low","risk_score":0.1,'
            '"summary":"","secondary_intents":[]}\n```'
        )
        result = parse_response(raw)
        assert result is not None and result.risk_level == "low"

    def test_rejects_invalid_risk_level(self):
        payload = {
            "primary_intent": "x", "secondary_intents": [],
            "risk_level": "catastrophic", "risk_score": 0.9, "summary": "",
        }
        assert parse_response(json.dumps(payload)) is None

    def test_rejects_empty_primary_intent(self):
        payload = {
            "primary_intent": "", "secondary_intents": [],
            "risk_level": "low", "risk_score": 0.1, "summary": "",
        }
        assert parse_response(json.dumps(payload)) is None

    def test_drops_invalid_caller_entries(self):
        payload = {
            "primary_intent": "x", "secondary_intents": [],
            "risk_level": "low", "risk_score": 0.1, "summary": "",
            "caller_classifications": [
                {
                    "caller": "a::b",
                    "dependency_type": "nonsense",
                    "coupling": "tight",
                    "update_effort": "trivial",
                },
                {
                    "caller": "c::d",
                    "dependency_type": "test",
                    "coupling": "loose",
                    "update_effort": "trivial",
                },
            ],
        }
        result = parse_response(json.dumps(payload))
        assert result is not None
        assert len(result.caller_classifications) == 1
        assert result.caller_classifications[0].caller == "c::d"

    def test_clamps_risk_score_to_unit_range(self):
        payload = {
            "primary_intent": "x", "secondary_intents": [],
            "risk_level": "critical", "risk_score": 2.5, "summary": "",
        }
        result = parse_response(json.dumps(payload))
        assert result is not None and result.risk_score == 1.0

    def test_build_prompt_contains_function_source(self, graph):
        fn = graph.functions["modules/pricing.py::calculate_price"]
        ctx = FunctionContext(fn=fn, source="def calculate_price(): pass", callers=[])
        prompt = build_prompt(ctx)
        assert "calculate_price" in prompt
        assert "modules/pricing.py" in prompt
        assert "no callers" in prompt.lower()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TestImpactStore:
    def test_roundtrip(self, tmp_path):
        store = ImpactStore(tmp_path)
        impact = ImpactFile(
            functions={"x::f": _report()},
            meta=ImpactMeta(llm_model="m", functions_analyzed=1),
        )
        store.save(impact)
        loaded = store.load()
        assert "x::f" in loaded.functions
        assert loaded.functions["x::f"].risk_level == "high"
        assert loaded.meta.llm_model == "m"

    def test_load_missing_returns_empty(self, tmp_path):
        empty = ImpactStore(tmp_path).load()
        assert empty.functions == {}

    def test_prune_removes_stale(self, tmp_path):
        impact = ImpactFile(functions={"alive::f": _report(), "dead::g": _report()})
        removed = ImpactStore.prune(impact, live_fn_ids={"alive::f"})
        assert removed == 1
        assert "dead::g" not in impact.functions

    def test_load_discards_outdated_schema_version(self, tmp_path):
        """A stale schema_version on disk triggers regeneration (returns empty)."""
        import json
        store = ImpactStore(tmp_path)
        store.store_dir.mkdir(parents=True, exist_ok=True)
        # Legacy v1 payload — what existed before the maxlen fix landed.
        store.path.write_text(
            json.dumps({
                "schema_version": "1",
                "meta": {},
                "functions": {
                    "x::f": {
                        "content_hash": "abc",
                        "risk_level": "low",
                        "risk_score": 0.1,
                        "summary": "old",
                        "safe_operations": ["add optional"],  # legacy slice
                        "dangerous_operations": ["change return"],
                        "action_plan": "",
                        "caller_classifications": [],
                    }
                },
            }),
            encoding="utf-8",
        )
        loaded = store.load()
        # Stale file treated as missing → next generator run refills it cleanly.
        assert loaded.functions == {}


# ---------------------------------------------------------------------------
# Tool integration
# ---------------------------------------------------------------------------

class TestScopeImpactIntegration:
    def test_scope_function_surfaces_impact(self, graph, tmp_path):
        fn_id = "modules/pricing.py::calculate_price"
        _write_impact(tmp_path, fn_id, _report(
            risk="high", score=0.7,
            caller_classifications=[CallerClassification(
                caller="api/prices.py::get_price",
                dependency_type="core_logic", coupling="tight",
                update_effort="moderate", note="Uses return directly",
            )],
        ))
        result = _tool_scope(graph, {"function": fn_id}, root=tmp_path)
        assert "impact" in result
        imp = result["impact"]
        assert imp["risk_level"] == "high"
        assert imp["risk_score"] == pytest.approx(0.7)
        assert imp["dangerous_operations"] == ["change return type"]
        assert imp["caller_classifications"][0]["caller"] == "api/prices.py::get_price"

    def test_scope_function_no_impact_omits_section(self, graph, tmp_path):
        result = _tool_scope(
            graph, {"function": "modules/pricing.py::calculate_price"},
            root=tmp_path,
        )
        assert "impact" not in result


class TestScopeSimilarLogic:
    def test_similar_logic_groups_by_secondary_intent(self, graph, tmp_path):
        calc = graph.functions["modules/pricing.py::calculate_price"]
        other = graph.functions["api/prices.py::get_price"]
        saved_a, saved_b = calc.secondary_intents, other.secondary_intents
        calc.secondary_intents = ["pricing logic", "discount computation"]
        other.secondary_intents = ["pricing logic"]
        try:
            result = _tool_scope(graph, {"function": calc.id}, root=tmp_path)
            assert "similar_logic" in result
            tags = {g["intent"] for g in result["similar_logic"]}
            assert "pricing logic" in tags
            pricing_group = next(
                g for g in result["similar_logic"] if g["intent"] == "pricing logic"
            )
            assert other.id in pricing_group["also_in"]
        finally:
            calc.secondary_intents, other.secondary_intents = saved_a, saved_b


class TestBeforeCreateImpactEnrichment:
    def test_change_affected_fns_carry_risk(self, graph, tmp_path):
        fn_id = "modules/pricing.py::calculate_price"
        _write_impact(tmp_path, fn_id, _report(risk="critical", score=0.95))
        result = _tool_before_create(
            graph, {"intent": "rename calculate_price to compute_price"}, tmp_path,
        )
        assert result["intent_type"] == "change"
        affected = result["functions"]["affected_fns"]
        target = next(e for e in affected if e["name"] == "calculate_price")
        assert target["risk_level"] == "critical"
        assert target["dangerous_operations"] == ["change return type"]

    def test_change_duplication_warning_on_secondary_intents(self, graph, tmp_path):
        calc = graph.functions["modules/pricing.py::calculate_price"]
        other = graph.functions["modules/inventory.py::check_stock"]
        saved_a, saved_b = calc.secondary_intents, other.secondary_intents
        calc.secondary_intents = ["cost calculation"]
        other.secondary_intents = ["cost calculation"]
        try:
            result = _tool_before_create(
                graph, {"intent": "rename calculate_price"}, tmp_path,
            )
            assert "similar_logic" in result
            tags = {g["intent"] for g in result["similar_logic"]}
            assert "cost calculation" in tags
        finally:
            calc.secondary_intents, other.secondary_intents = saved_a, saved_b


class TestHotspotsRiskLevel:
    def test_hotspots_include_risk_level_from_impact_file(self, graph, tmp_path):
        fn_id = "modules/pricing.py::calculate_price"
        _write_impact(tmp_path, fn_id, _report(risk="medium", score=0.5))
        result = _section_hotspots(graph, min_callers=1, root=tmp_path)
        target = next(h for h in result["hotspots"] if h["function"] == fn_id)
        assert target["risk_level"] == "medium"
        assert target["risk_score"] == pytest.approx(0.5)

    def test_hotspots_no_impact_no_risk_field(self, graph, tmp_path):
        result = _section_hotspots(graph, min_callers=1, root=tmp_path)
        for h in result["hotspots"]:
            assert "risk_level" not in h


# ---------------------------------------------------------------------------
# FunctionNode model
# ---------------------------------------------------------------------------

def test_function_node_default_secondary_intents_is_empty():
    fn = FunctionNode(
        id="x::f", file="x.py", name="f", kind="function", language="python",
        line_start=1, line_end=2, params=[],
    )
    assert fn.secondary_intents == []


def test_graph_serialises_secondary_intents(tmp_path):
    from winkers.store import GraphStore

    g = Graph()
    g.functions["a::f"] = FunctionNode(
        id="a::f", file="a.py", name="f", kind="function", language="python",
        line_start=1, line_end=2, params=[],
        secondary_intents=["email validation"],
    )
    (tmp_path / ".winkers").mkdir()
    GraphStore(tmp_path).save(g)
    loaded = GraphStore(tmp_path).load()
    assert loaded is not None
    assert loaded.functions["a::f"].secondary_intents == ["email validation"]


def test_before_create_change_accepts_root(graph, tmp_path):
    """Regression: _before_create_change must accept root= without error."""
    targets = ResolvedTargets(functions=["modules/pricing.py::calculate_price"])
    result = _before_create_change(
        graph, "rename calculate_price", targets,
        explicit_fns=list(targets.functions), root=tmp_path,
    )
    assert result["intent_type"] == "change"


# ---------------------------------------------------------------------------
# Provider dispatch — Ollama is now a first-class backend for impact
# ---------------------------------------------------------------------------

class TestProviderDispatch:
    def test_ollama_provider_is_accepted(self, graph, tmp_path):
        """_resolve_provider() returns OllamaProvider when config.provider='ollama'."""
        from winkers.impact.generator import ImpactGenerator
        from winkers.intent.provider import OllamaProvider

        (tmp_path / ".winkers").mkdir()
        (tmp_path / ".winkers" / "config.toml").write_text(
            '[intent]\nprovider = "ollama"\nmodel = "gemma3:4b"\n',
            encoding="utf-8",
        )
        gen = ImpactGenerator(graph, tmp_path)
        provider = gen._resolve_provider()  # noqa: SLF001
        assert isinstance(provider, OllamaProvider)

    def test_none_provider_returns_none(self, graph, tmp_path):
        from winkers.impact.generator import ImpactGenerator

        (tmp_path / ".winkers").mkdir()
        (tmp_path / ".winkers" / "config.toml").write_text(
            '[intent]\nprovider = "none"\n', encoding="utf-8",
        )
        gen = ImpactGenerator(graph, tmp_path)
        assert gen._resolve_provider() is None  # noqa: SLF001

    def test_call_ollama_provider_sends_format_json(self, monkeypatch):
        """Ollama HTTP call must include format=json to force structured output."""
        from winkers.impact.generator import _call_ollama_provider
        from winkers.intent.provider import IntentConfig, OllamaProvider

        captured = {}

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "response": (
                        '{"primary_intent":"x","risk_level":"low",'
                        '"risk_score":0.1,"summary":"","secondary_intents":[]}'
                    )
                }

        def fake_post(url, json, timeout):
            captured["url"] = url
            captured["json"] = json
            return _FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        provider = OllamaProvider(IntentConfig(provider="ollama", model="gemma3:4b"))
        raw = _call_ollama_provider(provider, "some prompt")
        assert raw is not None
        assert captured["json"]["format"] == "json"
        assert captured["json"]["model"] == "gemma3:4b"
        assert "api/generate" in captured["url"]
