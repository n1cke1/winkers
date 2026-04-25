"""Prompts for the description-author subsystem.

Two prompts (function vs template section) share a common output schema
so the coupling aggregator can compare artifacts across kinds. Both
prompts hardcode:

- 70-120 word prose with verb-first opening
- A REQUIREMENT to include 2-3 domain phrases users would actually type
  (this is what closed the lexical gap on T-FC3170-style queries during
  the Phase 0 spike — soft "weave synonyms" was insufficient)
- One mandatory non-trivial implementation detail
- Banned generic phrases
- Structured JSON output with hardcoded_artifacts

Prompts are written in English (universal for any codebase) but the LLM
is instructed to match the input's primary language, so a Russian-comment
project naturally yields Russian descriptions with domain terms preserved.
"""

from __future__ import annotations

from textwrap import dedent

# ---------------------------------------------------------------------------
# Shared rule blocks — composed into both prompts
# ---------------------------------------------------------------------------

_DESCRIPTION_RULES = dedent("""
    DESCRIPTION RULES:

    - 70-120 words, prose only — no markdown lists, headers, or code blocks.
    - Open with an action verb in 3rd person ("Извлекает...", "Builds...",
      "Renders...", "Solves...").
    - First sentence: WHAT — the observable effect, in domain terms.
    - Second sentence: WHEN — call site / trigger / invocation context. For
      entry points (HTTP routes, UI tabs) describe the trigger instead.
    - MUST include 2-3 DOMAIN PHRASES users would actually type when looking
      for this code. Not just identifiers from source — human concepts:
        * "коллекторы пара" alongside `prod_merge` / `cond_merge`
        * "доля регенерации" alongside `K_regen`
        * "13 ата / 9 ата" alongside pressure constants
        * "monthly load curve" alongside `calc_monthly_loads`
      Without these, the embedding fails on domain-vocabulary queries.
    - End with ONE non-trivial detail — something that would silently break
      under a naive edit. Examples: "round() applies ONLY to indices 17..24",
      "renaming the id breaks JS without console error", "deepcopy fails on
      this config — use json.loads(json.dumps())".
    - Match the language used in the input (docstrings/comments). Russian
      input → Russian output. English input → English output. Mixed input →
      mixed naturally — keep domain terms as-is.

    BANNED phrases:
    - "this function" / "эта функция"
    - "auxiliary" / "helper" / "вспомогательная" (without specifics)
    - "used in various places" / "используется в разных местах"
    - "handles X" without saying HOW
""").strip()


_OUTPUT_SCHEMA = dedent("""
    Output JSON ONLY — no markdown fences, no commentary. Schema:

    {
      "description": "<70-120 words of prose, per the rules above>",
      "hardcoded_artifacts": [
        {
          "value": "<canonical form>",
          "kind": "count | identifier | id_list | phrase | threshold | route | other",
          "context": "<one phrase: what this value means here>",
          "surface": "<optional: original text if different from value>"
        }
      ]
    }

    CANONICALIZATION of `value`:
    - Numbers: bare digit string — "33", not "33 переменных"
    - Identifier lists: JSON array, sorted alphabetically — ["PT1","PT2","PT6","R3","R4","T5"]
    - Single identifiers: bare name — "K_regen", "build_constraints"
    - Phrases: lowercased, normalized whitespace — "доля регенерации"
    - Routes: full path with method when relevant — "POST /api/calculate"

    INCLUDE in hardcoded_artifacts (load-bearing — change here forces change elsewhere):
    - Counters/sizes that surface elsewhere as literals (e.g. len(X) == 33
      and HTML displays "33 переменных")
    - Lists of identifiers duplicated across files
    - Magic numbers downstream code depends on (range bounds, thresholds)
    - Route paths referenced by both backend and frontend
    - Domain phrases that templates copy verbatim

    EXCLUDE:
    - Generic constants (0, 1, "", None, True, False)
    - Locally-scoped values (loop bounds, intermediate calculations)
    - Language idioms (str.join(""), [].push, etc.)
    - Array indices that aren't load-bearing
    - Logging/error message strings
    - CSS classes, colors, layout constants

    If nothing qualifies as load-bearing, return an empty array. DO NOT
    invent artifacts to fill space — false positives noise the coupling
    aggregator more than they help.
""").strip()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_FUNCTION_PROMPT = """\
You are documenting a code function for a semantic search index that
helps a developer find the right place to work given a natural-language
task description.

{description_rules}

{output_schema}

EXAMPLES of good hardcoded_artifacts for a code function:
- A constraint-builder lists `IDX size` as count, the number of constraint
  groups it emits, and any threshold it compares against.
- An API route handler lists its own route path, the body fields it expects,
  and any enum-like string values it dispatches on.
- A factory/builder lists the identifier list it iterates through.

SOURCE CODE FOLLOWS. After the source, 1-2 nearest callers are shown as
additional context (signatures only, not bodies — to keep cache invalidation
tight; a caller's body change should not dirty this description).
"""


_TEMPLATE_SECTION_PROMPT = """\
You are documenting a UI section (a tab, panel, or block of an HTML
template) for a semantic search index. The agent uses descriptions to
find the right place to make UI changes given a natural-language task.

{description_rules}

{output_schema}

EXAMPLES of good hardcoded_artifacts for a template section:
- Counter text "33 переменных" → value="33", kind="count",
  surface="33 переменных", context="counter of MILP variables".
- Inline domain value "K_regen ≈ 0.265" → value="0.265", kind="threshold",
  context="typical K_regen baseline shown in approach text".
- DOM ids that JS reads (`<text id="val-PT1-heat">...`) → kind="identifier",
  context="id contract with tab_scheme.js _setText".
- A turbine name list embedded directly in HTML → kind="id_list".

DO NOT extract: button labels (OK/Cancel), CSS class names, color codes,
layout constants like padding/margin, generic placeholder text such as
"Загрузка..." or "—".

For prose: describe WHAT the user sees AND WHICH BACKEND DATA populates
it. Look for `{{{{ jinja_vars }}}}`, ids matching JS conventions
(`val-*`, `inp-*`, `pane-*`), fetch URLs in onclick/data-attrs, and
reference them. If the section is mostly a placeholder filled by JS,
say so and name the likely JS file based on naming convention
(pane-X / tab_X.js → static/js/tab_X.js).

SECTION HTML FOLLOWS. A neighboring-section list and any leading HTML
comment are provided for context.
"""


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def format_function_prompt(
    fn_source: str,
    file_path: str,
    fn_name: str,
    callers: list[str] | None = None,
) -> str:
    """Render the full function-unit prompt.

    `callers` — list of caller signatures (NOT bodies). Body inclusion would
    dirty the cache whenever a caller's internal logic changes; signatures
    are stable enough to give "called from where" context.
    """
    body = _FUNCTION_PROMPT.format(
        description_rules=_DESCRIPTION_RULES,
        output_schema=_OUTPUT_SCHEMA,
    )
    parts = [
        body.rstrip(),
        "",
        f"FUNCTION: {fn_name}  ({file_path})",
        "```",
        fn_source.strip(),
        "```",
    ]
    if callers:
        parts.append("")
        parts.append("NEAREST CALLERS (signatures only, no bodies):")
        for c in callers[:3]:
            parts.append(f"  - {c}")
    return "\n".join(parts)


_DATA_FILE_PROMPT = """\
You are documenting a structured data file (JSON / YAML / TOML) for
a semantic search index. The agent uses these descriptions to find
which data file holds a value when given a natural-language task.

{description_rules}

{output_schema}

EXAMPLES of good hardcoded_artifacts for a data file:
- Identifier lists used as foreign keys across code (turbine ids,
  scenario keys) — kind="id_list".
- Domain-specific numeric values whose semantics are encoded in
  surrounding keys (a coefficient labeled "K_regen" alongside its
  numeric value) — kind="threshold", with `surface` carrying the
  full key=value phrase.
- Route / endpoint paths embedded in mock or default configs —
  kind="route".
- Field/key names that mirror schema in code or templates — value
  is the key string, kind="identifier".

DO NOT extract:
- Cached or computed values (timestamps, content hashes,
  file mtimes encoded as integers).
- Pure layout / cosmetic numbers (x/y/w/h coordinates, RGB colors,
  font sizes). They aren't load-bearing semantics, just visual.
- Keys/values that are auto-generated by tooling and not authored.
- Long-tail unique identifiers (UUIDs, hash digests).

PROSE GUIDANCE FOR DATA FILES:
- Open with what the file represents in the project (single sentence).
- Then which code paths produce/consume it (file::fn references).
- Mention the structure briefly (top-level keys, item count for
  arrays).
- End with the non-trivial detail — a constraint other code
  depends on. For tespy_topology.json: "edge labels must match
  TESPy connection labels in chp_network.py::_build's add()".
  For tespy_formulas.json: "id field is the key joining frontend
  /api/tespy/formulas card lookup to backend get_equation_catalog
  output".

DATA FILE FOLLOWS. Path and the file content are below.
"""


def format_data_file_prompt(
    file_content: str,
    file_path: str,
) -> str:
    """Render the full data-file prompt."""
    body = _DATA_FILE_PROMPT.format(
        description_rules=_DESCRIPTION_RULES,
        output_schema=_OUTPUT_SCHEMA,
    )
    parts = [
        body.rstrip(),
        "",
        f"FILE: {file_path}",
        "```",
        file_content.strip(),
        "```",
    ]
    return "\n".join(parts)


def format_template_section_prompt(
    section_html: str,
    file_path: str,
    section_id: str,
    leading_comment: str = "",
    neighbor_section_ids: list[str] | None = None,
) -> str:
    """Render the full template-section prompt."""
    body = _TEMPLATE_SECTION_PROMPT.format(
        description_rules=_DESCRIPTION_RULES,
        output_schema=_OUTPUT_SCHEMA,
    )
    parts = [
        body.rstrip(),
        "",
        f"SECTION: #{section_id}  ({file_path})",
    ]
    if leading_comment:
        parts.append(f"LEADING COMMENT: {leading_comment}")
    if neighbor_section_ids:
        parts.append(f"NEIGHBOR SECTIONS: {', '.join(neighbor_section_ids)}")
    parts.extend([
        "",
        "```html",
        section_html.strip(),
        "```",
    ])
    return "\n".join(parts)
