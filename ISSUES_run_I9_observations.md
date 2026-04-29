# Run I9 (S-WNK, 2026-04-29) — six observations

**Source run:** `.run-artifacts/I9-2026-04-29/` (claude_output.json, claude_settings.json, diff.patch, prompt.txt, summary.json).
**Outcome:** task passed (263/0/232 — 31 added tests, no regressions). 70 turns, $2.64, 882s.
**Tool stats:** ToolSearch 7, orient 1, find_work_area 2, before_create 5, Read 24, Glob 4, Edit 12, Write 1, Bash 7.

The run **succeeded**, so these are workflow / efficiency / coverage observations, not failures. None of them blocked completion of I9, but each represents a gap that compounds at scale (longer sessions, harder tasks, traps).

Verified against `winkers 0.8.4 @ 1e132db` (current main on this VPS).

---

## Issue 1 — `find_work_area` blind to class-level attributes

**Observation.** Embeddings/`find_work_area` index only function units. Relationship-style class-body assignments — `contracts = relationship("Contract", back_populates="client")` — aren't found by semantic search. Agent has to read every model file by hand.

**Verified.** `descriptions/aggregator.py:45` defines `unit_kind` as `"function_unit" | "traceability_unit"` — that's the entire unit taxonomy. `embeddings/builder.py:266` (`_embed_text_for`) embeds `name + description` of those units only. SQLAlchemy `relationship`, Django field declarations, Pydantic `Field`, dataclass fields, React props — none enter the index.

**Impact in this run.** Task is "audit soft-delete consistency"; the cited blind spots are exactly relationship traversals (`client.invoices`, `invoice.line_items`, `contract.invoices`). Of the 24 `Read` calls, a chunk goes to model-file enumeration that `find_work_area` should have answered.

**Fix path.** Add a third unit kind, e.g. `attribute_unit`. Source: tree-sitter query for class-body `(annotated_)assignment` nodes whose RHS is a `call` (or for SQLAlchemy/Pydantic specifically — annotated `: Mapped[...]` etc). `embed_text` = `Class.attr` + RHS-call name + (optional) one-line description from the existing description-author pipeline. Reuses the same incremental cache. Cost: medium-large — new tree-sitter queries per language profile, schema bump in `unit.kind` consumers (likely 5–10 sites).

**Severity:** P1 (compounds Issue 4 — these attrs are also what users name in batch intents).

---

## Issue 2 — description language drifts to source-text language

**Observation.** `BaseRepo.get_all` returned a Russian description in an English codebase.

**Verified.** `descriptions/prompts.py:48-49` instructs the LLM: *"Match the language used in the input (docstrings/comments). Russian input → Russian output. English input → English output."* Author intent is to preserve native domain terms in non-English projects, but in mixed-language code (an English repo with one Russian comment block), per-unit drift is expected.

**Impact in this run.** Single hit observed; minor in I9 because BGE-M3 is multilingual — embedding similarity still works. The pain is human/agent readability when scanning `find_work_area` results: a single Russian description in an otherwise English summary creates noise.

**Fix paths (ranked):**
- **B (recommended) — detect dominant language at init, lock it.** During `winkers init`, sample N source files, run a fast lang detector, set `project_language` in `.winkers/config.json`. Author prompt becomes "Generate descriptions in `<project_language>` regardless of input fragments." Loses the deliberate native-term feature for *mixed-language* projects, keeps it for clean Russian ones.
- **A — force English universally.** Simpler. Loses domain-flavor for monolingual Russian projects.
- **D — normalize at indexing.** If detected description language differs from project language, regenerate. More compute, simplest behavior.

**Severity:** P3 (annoyance, not blocker).

---

## Issue 3 — hooks not logged

**Observation.** `PreToolUse`, `PostToolUse`, `UserPromptSubmit` produce no logged output anywhere — no stdout capture, no timing, no outcome record. Impossible to audit overhead vs benefit.

**Verified.** `hooks/pre_write.py`, `hooks/post_write.py`, `hooks/prompt_enrich.py` all return JSON to stdout for Claude Code consumption and exit. None write to a per-session log. The benchmark sees only the MCP-tool list.

**Impact.** This is a **meta-issue**: every other observation is harder to validate without it. We can't tell from the artifacts whether the 30s `PostToolUse` ever timed out, whether `pre-write` denials fired, whether `prompt-enrich` actually injected anything. For 0.9.x, observability has to come *before* fixes that need to be measured.

**Fix.** Append-only JSON-Lines log at `.winkers/hooks.log` (or under `.winkers/sessions/<id>/`). Each hook emits one line:
```
{"ts": "...", "event": "PostToolUse", "file": "app/repos/invoice.py",
 "duration_ms": 1240, "decision": "allow", "warnings_emitted": 1}
```
Wrap each `run()` in a `try/finally` recorder. Sub-day implementation.

**Severity:** P0 for the development workflow (gates measurement of every other fix).

---

## Issue 4 — `before_create` resolver hard-fails on batch / attribute intents

**Observation.** `"fix Client.invoices, Client.payments, Client.contracts"` → resolver gives a generic error.

**Verified.** `target_resolution.py:106-108` `_CLASS_METHOD_RE = r"\b([A-Z]\w*)\.([A-Za-z_]\w*)\s*\("` — requires a trailing `(`. `Class.attr` (no parens, no call) doesn't match. The regex captures only **method calls**, not attribute accesses. Falls through to fuzzy match, where bare `invoices`/`payments`/`contracts` aren't function names (Issue 1: also not in graph as units), so resolution returns empty → `_before_create_unknown` fires with the generic error.

**Impact in this run.** Agent did 5 `before_create` calls for I9; some likely shaped to avoid this trap. On more complex tasks the failure mode is more frequent.

**Fix path (small, two parts):**
1. **Attribute pattern.** Add a regex `_CLASS_ATTR_RE = r"\b([A-Z]\w*)\.([a-z]\w*)\b(?!\s*\()"` that captures `Class.attr` when there's no following `(`. Seat it after `_CLASS_METHOD_RE` in `extract_explicit_targets`.
2. **Comma / `and` enumeration.** No code change needed if (1) lands — the regex iterates over the whole intent and picks up each occurrence. Worth a unit test asserting `"fix Client.invoices, Client.payments, Client.contracts"` resolves to three targets.
3. **Structured-hint fallback.** When resolution stays empty after both regexes, the response should suggest a syntax instead of a flat error: `"Could not resolve. Try Class.method() for methods or Class.attribute (without parens) for fields."` — `_before_create_unknown` (line 1902) already returns an `error` field; just enrich it.

**Severity:** P2 — small fix, but until Issue 1 lands, attribute targets resolve to nothing in the graph anyway. (1) and (4) should ship together.

---

## Issue 5 — `pre-write` doesn't enforce `before_create` for locked edits

**Observation.** Agent edited a locked function (calls 57–58) without prior `before_create`; `pre-write` didn't block.

**Verified.** `hooks/pre_write.py` (101 lines, full read). It does **only** AST-hash duplicate detection: load graph → check exact/near clones in the new content → deny if exact, warn if near. There is no:
- session-state lookup of registered `before_create` intents,
- check of whether the target function is `locked` in the graph,
- coupling between the MCP `before_create` tool result and subsequent `Edit` permission.

**Impact.** The semantic gate (CLAUDE.md says: "before_create is required before ANY code change") is currently advisory — the agent reads it, may follow it, but the hook doesn't enforce it.

**Fix path:**
1. **Session state for registered intents.** When `before_create` is called via MCP, record `{intent_hash, resolved_targets, ts}` into `.winkers/sessions/<id>/intents.json`. Already have `winkers/session/state.py` for `WriteEvent` / `Warning` — extend with `RegisteredIntent`.
2. **Pre-write enforcement.** In `pre_write.run()`, for the file being edited:
   - Look up `function_ids` whose `lock_status == "locked"` and whose body region overlaps the edit.
   - Cross-check against current session's registered intents. If a locked fn is being edited and no intent covers it → deny with a structured reason: `"Locked function X.foo() — call before_create({intent: ...}) first."`
3. **Bypass.** Allow an explicit `WINKERS_BYPASS_INTENT_GATE=1` env var or a `winkers ack <fn_id>` CLI for cases where the agent has already read the convention but the MCP call genuinely doesn't add info.

**Severity:** P1. Closes a real gap, but until Issue 6 (debounce) is addressed, adding more pre-write work is undesirable. Sequence: 6 → 5.

---

## Issue 6 — `PostToolUse` 30s timeout, no debounce

**Observation.** 13 edits × up to 30s = 6+ min from a 15-min session burned in the post-write hook.

**Verified.** `claude_settings.json` confirms `PostToolUse` timeout = 30. `hooks/post_write.py:42-118` runs the full pipeline on each edit:
1. `snapshot_signatures` (in-memory diff baseline)
2. `store.update_files(graph, [rel_path])` — re-parse + AST + value_locked refresh for the edited file
3. `store.save(graph)` — persist
4. `compute_diff` (signature/added/removed)
5. `diff_collections` (value_locked diff)
6. `_coherence_check`
7. Session-state mutation + save

No batching, no async deferral, no skipping when the file's content_hash is unchanged from a prior intermediate edit.

**Impact in this run.** Tool stats show 12 Edits + 1 Write. If most fired the full pipeline, the cumulative cost could be material. The summary's `duration_ms = 882_351` (~14m42s) is mostly LLM time, but hook seconds are **inside** the agent's effective wall clock — every second the hook spends is a second the agent waits on its next turn.

**Fix paths (ranked):**
1. **Skip on content-unchanged.** Cheap. Hash file bytes; if hash matches the last graph snapshot for that file, exit early. Catches no-op MultiEdit regions, idempotent reformat passes.
2. **Coalesce burst writes.** When multiple `Write|Edit` events hit within a debounce window (e.g. 500ms), only the last triggers the full pipeline. Implement via a session-state debounce timestamp: each hook reads-checks-writes a `last_full_run_ts` per file; if last was <500ms ago AND no new symbols, skip. Requires per-file lock to avoid races.
3. **Async deferral with synchronous coherence-only path.** Synchronous part = the warnings the agent needs to see (`VALUE_LOCKED`, `MODIFIED with callers`). Defer graph-save / embeddings / heavy diffs to a background worker. Riskier (subsequent edits race against an in-flight save), but pays back most for long sessions.
4. **Incremental graph update primitive.** `store.update_files` already takes a file list; the cost driver is per-file work. Profile to see if the per-file work itself can be cheapened (today: full re-parse + value_locked re-scan even for tiny edits).

Practical sequence: 1 → 2 → profile → 4. Defer 3 unless timing data shows 1+2+4 don't hit budget.

**Severity:** P0 for the I9-class workflow; tightly coupled to Issue 5 (don't add work to pre-write before post-write is faster).

---

## Recommended ordering

Each cluster ships independently; nothing here blocks the existing test suite.

**Wave 1 — observability (so we can measure):**
- **3 (logging).** Sub-day. Lands before everything else.

**Wave 2 — small UX wins:**
- **4 (resolver attrs/batch).** ≤ 1 day. Pair with…
- **2 (language lock).** ≤ 1 day, recommendation: option B (detect at init, lock).

**Wave 3 — performance, then enforcement:**
- **6 (post-write debounce).** 2–3 days. Profiling-driven.
- **5 (pre-write intent gate).** 2–3 days, after 6.

**Wave 4 — index expansion:**
- **1 (class-level attribute units).** Largest item; new unit kind end-to-end. ≥ 1 week.

Wave 1 first; Waves 2–3 in parallel; Wave 4 plannable separately.

---

## Cross-references

- Issue 5 of the previous report (`ISSUE_impact_literal_blind.md`, 2026-04-28) is the related but distinct **Path 4** (cross-file consumer detection). That issue is about `value_locked.py`, not the hooks. The two issue files together describe most of the structural blind spots in the current `winkers` warning gate.
