"""Impact + intent generator — batch LLM pass at `winkers init` time.

Runs AFTER graph + call_edges are built. For each affected function:
  1. Assemble FunctionContext (source, callers, call sites).
  2. Compute content_hash over (function source + all callers' sources).
  3. If hash matches the existing impact.json entry → skip (cached).
  4. Otherwise call LLM with the combined prompt, parse result, write
     intent fields onto the FunctionNode and ImpactReport into ImpactFile.

Concurrency via thread pool (LLM clients here are sync).
Progress via click.progressbar when a click context is available.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from winkers.impact.models import (
    AnalysisResult,
    CallerInfo,
    FunctionContext,
    ImpactFile,
    ImpactMeta,
    ImpactReport,
)
from winkers.impact.prompt import build_prompt, parse_response
from winkers.impact.store import ImpactStore
from winkers.intent.provider import load_config
from winkers.models import FunctionNode, Graph
from winkers.store import STORE_DIR

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "enabled": True,
    "max_callers_in_prompt": 10,
    "batch_concurrency": 3,
    "min_callers_for_analysis": 0,
    "timeout_seconds": 30,
}


def load_impact_config(root: Path) -> dict:
    """Read [impact] section from .winkers/config.toml."""
    path = root / STORE_DIR / "config.toml"
    cfg = dict(_DEFAULTS)
    if not path.exists():
        return cfg
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("impact", {})
        for k, default in _DEFAULTS.items():
            if k in section:
                cfg[k] = section[k]
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ImpactGenerator:
    """Orchestrates the impact+intent batch pass.

    Usage:
        gen = ImpactGenerator(graph, root)
        impact_file = gen.run(progress=click.progressbar)
    """

    def __init__(
        self,
        graph: Graph,
        root: Path,
        force: bool = False,
    ) -> None:
        self.graph = graph
        self.root = root
        self.force = force
        self.cfg = load_impact_config(root)
        self.intent_cfg = load_config(root)
        self._source_cache: dict[str, str] = {}

    def run(self, impact_file: ImpactFile | None = None, progress_factory=None) -> ImpactFile:
        """Generate impact + intent for affected functions. Returns updated ImpactFile.

        `progress_factory` — optional callable returning a context manager that
        supports `.update(1)`. Pass `click.progressbar` to get CLI progress.
        """
        started = datetime.now(UTC)
        if impact_file is None:
            impact_file = ImpactStore(self.root).load()

        live_fn_ids = set(self.graph.functions.keys())
        ImpactStore.prune(impact_file, live_fn_ids)

        provider = self._resolve_provider()
        if provider is None:
            log.info("impact: no LLM provider available, skipping analysis")
            return impact_file

        # Pick affected functions. min_callers_for_analysis filters leaves.
        min_callers = int(self.cfg.get("min_callers_for_analysis", 0))
        candidates: list[FunctionContext] = []
        for fn_id, fn in self.graph.functions.items():
            ctx = self._build_context(fn)
            if len(ctx.callers) < min_callers:
                continue
            # Hash-based skip
            content_hash = _content_hash(ctx)
            if not self.force:
                existing = impact_file.functions.get(fn_id)
                if existing and existing.content_hash == content_hash and fn.intent:
                    continue
            candidates.append(ctx)

        analyzed = 0
        failed = 0
        skipped = len(self.graph.functions) - len(candidates)

        if not candidates:
            impact_file.meta = _build_meta(
                provider_model=self._provider_model(provider),
                analyzed=0, skipped=skipped, failed=0,
                started=started,
            )
            return impact_file

        concurrency = int(self.cfg.get("batch_concurrency", 3))
        max_callers = int(self.cfg.get("max_callers_in_prompt", 10))

        pb_cm = None
        if progress_factory is not None:
            try:
                pb_cm = progress_factory(
                    length=len(candidates), label="Impact analysis",
                )
            except Exception:
                pb_cm = None

        if pb_cm is not None:
            with pb_cm as bar:
                analyzed, failed = self._run_batch(
                    provider, candidates, impact_file,
                    concurrency=concurrency, max_callers=max_callers,
                    progress=bar,
                )
        else:
            analyzed, failed = self._run_batch(
                provider, candidates, impact_file,
                concurrency=concurrency, max_callers=max_callers,
                progress=None,
            )

        impact_file.meta = _build_meta(
            provider_model=self._provider_model(provider),
            analyzed=analyzed, skipped=skipped, failed=failed,
            started=started,
        )
        return impact_file

    # -- internals -----------------------------------------------------------

    def _resolve_provider(self):
        """Claude API preferred, Ollama supported (via format=json + retry).

        NoneProvider → skip the impact pass entirely. Any other configured
        provider (Api or Ollama) participates — quality tradeoff is on the
        user who chose the small local model.
        """
        from winkers.intent.provider import (
            ApiProvider,
            NoneProvider,
            OllamaProvider,
            auto_detect,
        )

        provider = auto_detect(self.intent_cfg)
        if isinstance(provider, NoneProvider):
            return None
        if isinstance(provider, (ApiProvider, OllamaProvider)):
            return provider
        log.info(
            "impact: provider=%s is not supported for combined analysis",
            type(provider).__name__,
        )
        return None

    def _provider_model(self, provider) -> str:
        return getattr(provider, "model", "") or self.intent_cfg.api_model or self.intent_cfg.model

    def _run_batch(
        self, provider, ctxs: list[FunctionContext], impact_file: ImpactFile,
        concurrency: int, max_callers: int, progress,
    ) -> tuple[int, int]:
        analyzed = 0
        failed = 0

        def worker(ctx: FunctionContext):
            prompt = build_prompt(ctx, max_callers=max_callers)
            # Up to 3 attempts — mainly helps Ollama, which can occasionally
            # return half-JSON even with format=json.
            for _ in range(3):
                raw = _call_provider(provider, prompt)
                if not raw:
                    continue
                parsed = parse_response(raw)
                if parsed is not None:
                    return ctx, parsed
            return ctx, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for ctx, result in pool.map(worker, ctxs):
                if result is None:
                    failed += 1
                else:
                    self._apply_result(ctx, result, impact_file)
                    analyzed += 1
                if progress is not None:
                    try:
                        progress.update(1)
                    except Exception:
                        pass

        return analyzed, failed

    def _apply_result(
        self, ctx: FunctionContext, result: AnalysisResult, impact_file: ImpactFile,
    ) -> None:
        fn = self.graph.functions.get(ctx.fn.id)
        if fn is None:
            return

        fn.intent = result.primary_intent
        fn.secondary_intents = list(result.secondary_intents)

        content_hash = _content_hash(ctx)
        impact_file.functions[ctx.fn.id] = ImpactReport(
            content_hash=content_hash,
            risk_level=result.risk_level,
            risk_score=result.risk_score,
            summary=result.summary,
            caller_classifications=list(result.caller_classifications),
            safe_operations=list(result.safe_operations),
            dangerous_operations=list(result.dangerous_operations),
            action_plan=result.action_plan,
        )

    def _build_context(self, fn: FunctionNode) -> FunctionContext:
        source = self._function_source(fn)
        callers: list[CallerInfo] = []

        # Callers, sorted by caller's fan-in (hotter first = more coupled signal).
        caller_edges = self.graph.callers(fn.id)
        ranked = []
        for e in caller_edges:
            caller_fn = self.graph.functions.get(e.source_fn)
            if caller_fn is None:
                continue
            caller_fan_in = len(self.graph.callers(caller_fn.id))
            ranked.append((caller_fan_in, e, caller_fn))
        ranked.sort(key=lambda x: x[0], reverse=True)

        for _, edge, caller_fn in ranked:
            callers.append(CallerInfo(
                name=caller_fn.id,
                filepath=caller_fn.file,
                source=self._function_source(caller_fn),
                call_context=(
                    f"{edge.call_site.file}:{edge.call_site.line}: "
                    f"{edge.call_site.expression}"
                ),
            ))

        callees = [e.target_fn for e in self.graph.callees(fn.id)]
        return FunctionContext(fn=fn, source=source, callers=callers, callees=callees)

    def _function_source(self, fn: FunctionNode) -> str:
        file_src = self._file_source(fn.file)
        if not file_src:
            return ""
        lines = file_src.splitlines()
        start = max(0, fn.line_start - 1)
        end = min(len(lines), fn.line_end)
        return "\n".join(lines[start:end])

    def _file_source(self, rel_path: str) -> str:
        cached = self._source_cache.get(rel_path)
        if cached is not None:
            return cached
        path = self.root / rel_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        self._source_cache[rel_path] = text
        return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_hash(ctx: FunctionContext) -> str:
    """Stable hash over function source + every caller's source."""
    h = hashlib.sha256()
    h.update(ctx.source.encode("utf-8", errors="replace"))
    # Sort callers by name so ordering doesn't perturb the hash.
    for c in sorted(ctx.callers, key=lambda x: x.name):
        h.update(b"|")
        h.update(c.name.encode("utf-8", errors="replace"))
        h.update(b":")
        h.update(c.source.encode("utf-8", errors="replace"))
    return h.hexdigest()


def _call_provider(provider, prompt: str) -> str | None:
    """Dispatch the combined prompt to the right backend.

    The stock IntentProvider.generate() only handles the legacy
    one-sentence intent path — for impact we need full structured JSON, so
    we bypass it and issue the call directly using the provider's fields.
    """
    from winkers.intent.provider import ApiProvider, OllamaProvider

    if isinstance(provider, ApiProvider):
        return _call_api_provider(provider, prompt)
    if isinstance(provider, OllamaProvider):
        return _call_ollama_provider(provider, prompt)
    return None


def _call_api_provider(provider, prompt: str) -> str | None:
    try:
        resp = provider._client.messages.create(  # noqa: SLF001 — shared client
            model=provider.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        return text.strip() if text else None
    except Exception as e:
        log.debug("api impact LLM call failed: %s", e)
        return None


def _call_ollama_provider(provider, prompt: str) -> str | None:
    """Hit Ollama /api/generate with format=json to force structured output.

    format=json is a hard constraint — Ollama will keep generating until it
    closes all braces. Small local models still occasionally produce
    under-specified fields, which is why the batch worker retries the
    whole call a few times.
    """
    import httpx

    try:
        resp = httpx.post(
            f"{provider.url}/api/generate",
            json={
                "model": provider.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": provider.temperature,
                    "num_predict": 2000,
                },
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        return text if text else None
    except Exception as e:
        log.debug("ollama impact LLM call failed: %s", e)
        return None


def _build_meta(
    provider_model: str, analyzed: int, skipped: int, failed: int,
    started: datetime,
) -> ImpactMeta:
    duration = (datetime.now(UTC) - started).total_seconds()
    return ImpactMeta(
        generated_at=datetime.now(UTC).isoformat(),
        llm_model=provider_model,
        functions_analyzed=analyzed,
        functions_skipped=skipped,
        functions_failed=failed,
        duration_seconds=round(duration, 1),
    )
