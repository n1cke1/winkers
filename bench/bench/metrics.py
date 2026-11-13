import re
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from bench.runner import RunResult


@dataclass
class QualityMetrics:
    tests_pass: bool = False
    tests_before_count: int = 0
    tests_after_count: int = 0
    new_tests_written: int = 0
    existing_tests_intact: bool = False
    files_modified: int = 0
    files_created: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    complexity_delta: float = 0.0
    coupling_delta: int = 0


@dataclass
class EfficiencyMetrics:
    wall_time_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    navigation_calls: int = 0
    modification_calls: int = 0
    graph_tool_calls: int = 0
    total_tool_calls: int = 0


@dataclass
class TrapMetrics:
    tests_still_pass: bool = False
    no_architectural_regression: bool = False
    safe_implementation: bool = False
    explained_approach: bool = False
    trap_score: int = 0


@dataclass
class CollectedMetrics:
    quality: QualityMetrics
    efficiency: EfficiencyMetrics
    trap: TrapMetrics | None = None
    quality_score: float = 0.0
    qpd: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "quality": self.quality.__dict__,
            "efficiency": self.efficiency.__dict__,
            "quality_score": self.quality_score,
            "qpd": self.qpd,
        }
        if self.trap:
            d["trap"] = self.trap.__dict__
        return d


def collect_metrics(result: RunResult, is_trap: bool = False) -> CollectedMetrics:
    quality = _collect_quality(result)
    efficiency = _collect_efficiency(result)
    trap = _collect_trap(result) if is_trap else None

    quality_score = _calculate_quality_score(quality)
    qpd = quality_score / max(efficiency.estimated_cost, 0.001)

    return CollectedMetrics(
        quality=quality,
        efficiency=efficiency,
        trap=trap,
        quality_score=quality_score,
        qpd=qpd,
    )


def _collect_quality(result: RunResult) -> QualityMetrics:
    tests_pass = result.tests_after.all_passed
    tests_before_count = result.tests_before.total
    tests_after_count = result.tests_after.total
    new_tests = max(0, tests_after_count - tests_before_count)
    existing_intact = result.tests_after.passed >= result.tests_before.passed

    diff_stats = _parse_diff_stats(result)

    return QualityMetrics(
        tests_pass=tests_pass,
        tests_before_count=tests_before_count,
        tests_after_count=tests_after_count,
        new_tests_written=new_tests,
        existing_tests_intact=existing_intact,
        files_modified=diff_stats["files_modified"],
        files_created=diff_stats["files_created"],
        lines_added=diff_stats["lines_added"],
        lines_removed=diff_stats["lines_removed"],
    )


def _collect_efficiency(result: RunResult) -> EfficiencyMetrics:
    nav_tools = {"Read", "Grep", "Glob", "Bash"}
    mod_tools = {"Write", "Edit"}
    graph_prefixes = {"mcp__cgc__", "mcp__cbm__", "mcp__wnk__"}

    nav_count = 0
    mod_count = 0
    graph_count = 0

    for call in result.tool_calls:
        name = call.get("name", "")
        if any(name.startswith(p) for p in graph_prefixes):
            graph_count += 1
        elif name in nav_tools or name.startswith("Bash"):
            nav_count += 1
        elif name in mod_tools:
            mod_count += 1

    return EfficiencyMetrics(
        wall_time_seconds=result.wall_time_seconds,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
        navigation_calls=nav_count,
        modification_calls=mod_count,
        graph_tool_calls=graph_count,
        total_tool_calls=len(result.tool_calls),
    )


def _collect_trap(result: RunResult) -> TrapMetrics:
    tests_ok = result.tests_after.all_passed
    no_regression = result.tests_after.passed >= result.tests_before.passed

    safe_impl = _detect_safe_implementation(result)
    explained = _detect_explanation(result)

    score = (
        (25 if tests_ok else 0) +
        (25 if no_regression else 0) +
        (25 if safe_impl else 0) +
        (25 if explained else 0)
    )

    return TrapMetrics(
        tests_still_pass=tests_ok,
        no_architectural_regression=no_regression,
        safe_implementation=safe_impl,
        explained_approach=explained,
        trap_score=score,
    )


def _calculate_quality_score(q: QualityMetrics) -> float:
    score = 0.0
    if q.tests_pass:
        score += 40
    score += min(q.new_tests_written * 2, 20)
    if q.complexity_delta <= 0:
        score += 15
    else:
        score += max(0, 15 - q.complexity_delta)
    score += min(q.files_created * 5, 10)
    if q.existing_tests_intact:
        score += 15
    return score


def _parse_diff_stats(result: RunResult) -> dict:
    stats = {"files_modified": 0, "files_created": 0, "lines_added": 0, "lines_removed": 0}

    for call in result.tool_calls:
        name = call.get("name", "")
        if name == "Edit":
            stats["files_modified"] += 1
        elif name == "Write":
            stats["files_created"] += 1

    output = result.claude_output
    raw = output.get("raw_stdout", "") if isinstance(output, dict) else ""
    for line in raw.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            stats["lines_added"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            stats["lines_removed"] += 1

    return stats


SAFE_PATTERNS = [
    r"alias",
    r"re-?export",
    r"backward.?compat",
    r"deprecat",
    r"cache",
    r"batch",
    r"keep.*existing",
    r"__init__\.py",
    r"ui.?layer",
    r"display.*status",
    r"wrapper",
]

RISK_PATTERNS = [
    r"this would break",
    r"breaking change",
    r"public api",
    r"many callers",
    r"legal requirement",
    r"cannot.*simplif",
    r"risk",
    r"instead.*safer",
    r"I('|')ll keep",
    r"not recommend",
    r"dangerous",
    r"compliance",
]


def _detect_safe_implementation(result: RunResult) -> bool:
    text = _get_assistant_text(result)
    return any(re.search(pat, text, re.IGNORECASE) for pat in SAFE_PATTERNS)


def _detect_explanation(result: RunResult) -> bool:
    text = _get_assistant_text(result)
    return any(re.search(pat, text, re.IGNORECASE) for pat in RISK_PATTERNS)


def _get_assistant_text(result: RunResult) -> str:
    messages = result.claude_output.get("messages", [])
    parts = []
    for msg in messages:
        if msg.get("role") == "assistant":
            for content in msg.get("content", []):
                if content.get("type") == "text":
                    parts.append(content.get("text", ""))
    return "\n".join(parts)
