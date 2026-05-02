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

Descriptions are ALWAYS authored in English so the embedding space stays
monolingual. Domain-flavor for Russian-comment projects is preserved
in two ways: identifiers/values are kept as-is in the source language,
and incoming queries are pre-translated to English before semantic
search (see `winkers.descriptions.translator` + the prompt_enrich hook).
"""

from __future__ import annotations

from textwrap import dedent

# ---------------------------------------------------------------------------
# Shared rule blocks — composed into both prompts
# ---------------------------------------------------------------------------

_DESCRIPTION_RULES = dedent("""
    DESCRIPTION RULES:

    - 70-120 words, prose only — no markdown lists, headers, or code blocks.
    - Open with an action verb in 3rd person ("Builds...", "Renders...",
      "Extracts...", "Solves...").
    - First sentence: WHAT — the observable effect, in domain terms.
    - Second sentence: WHEN — call site / trigger / invocation context. For
      entry points (HTTP routes, UI tabs) describe the trigger instead.
    - MUST include 2-3 DOMAIN PHRASES users would actually type when looking
      for this code. Not just identifiers from source — human concepts:
        * "steam collectors" alongside `prod_merge` / `cond_merge`
        * "regeneration share" alongside `K_regen`
        * "13 / 9 atm pressure levels" alongside pressure constants
        * "monthly load curve" alongside `calc_monthly_loads`
      Without these, the embedding fails on domain-vocabulary queries.
    - End with ONE non-trivial detail — something that would silently break
      under a naive edit. Examples: "round() applies ONLY to indices 17..24",
      "renaming the id breaks JS without console error", "deepcopy fails on
      this config — use json.loads(json.dumps())".
    - Author the description in ENGLISH, regardless of the language used in
      source-code docstrings/comments. Keep code identifiers, file paths,
      and load-bearing domain values (e.g. "K_regen", "коллекторы пара",
      "tab_scheme.js") VERBATIM in their source language — only the
      surrounding prose is English. Incoming search queries are translated
      to English before lookup, so a uniformly-English embedding space
      gives more reliable retrieval than per-project language drift.

    BANNED phrases:
    - "this function"
    - "auxiliary" / "helper" (without specifics)
    - "used in various places"
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


# ---------------------------------------------------------------------------
# Class / attribute / value unit prompts (Wave 5a + Wave 4c-2)
# ---------------------------------------------------------------------------

_CLASS_PROMPT = """\
You are documenting a class for a semantic search index. The agent
uses descriptions to locate the right class to modify given a
natural-language task.

{description_rules}

{output_schema}

EXAMPLES of good hardcoded_artifacts for a class:
- Pinned status enum members the class checks against (kind="id_list").
- Schema column counter ("33 переменных") matching template text.
- Required base-class names downstream code depends on (kind="identifier").

PROSE GUIDANCE FOR CLASSES:
- Open with what the class represents in the domain (single sentence).
- Then state lifecycle: when instances are created, when they're destroyed,
  what mutates them (which methods).
- One non-trivial invariant a naive edit would break — e.g. "soft-deleted
  via deleted_at instead of row removal", "id field is the FK target for
  Invoice.client_id", "must call .commit() — auto-commit is OFF on this session".
- If this is a pure data model (Pydantic / dataclass / SQLAlchemy
  declarative), name the parent and the load-bearing field constraints.

CLASS METADATA + SOURCE FOLLOW. Methods are listed by signature only
(bodies elided to keep cache invalidation tight — a method body change
shouldn't dirty the class description on its own).
"""


_ATTRIBUTE_PROMPT = """\
You are documenting a class-body attribute for a semantic search index
— typically a SQLAlchemy `relationship`, Pydantic `Field`, dataclass
`field`, or similar configuration assignment.

{description_rules}

{output_schema}

EXAMPLES of good hardcoded_artifacts for a class attribute:
- The string target of a relationship (`relationship("Invoice", ...)`)
  → kind="identifier", value="Invoice".
- A `back_populates` partner key (kind="identifier").
- A FK column name when this is a `mapped_column(ForeignKey("orders.id"))`
  (kind="identifier", value="orders.id").
- A cascade specifier when the agent typically searches "cascade delete":
  → kind="phrase", value="all,delete-orphan".

PROSE GUIDANCE FOR ATTRIBUTES:
- Open with what role the attribute plays on its class (one sentence).
- Then explain the relationship type or field shape — "many-to-one
  back-populated by Invoice.client", "Pydantic constraint with default 0
  and ge=0".
- End with one non-trivial detail — a constraint that affects callers,
  e.g. "selectinload required upstream — direct attribute access otherwise
  triggers N+1", "Field(alias='client_id') — JSON payload uses
  snake_case, model uses snake_case, FastAPI emits camelCase".

ATTRIBUTE METADATA FOLLOWS — name (Class.attr), constructor call
(relationship/Field/...), type annotation if any, and source line.
"""


_VALUE_PROMPT = """\
You are documenting a module-level collection of literal values
(``set`` / ``frozenset`` / ``dict`` / ``Enum``) for a semantic search
index. The agent uses descriptions to locate the right collection
when removing/renaming a domain value.

{description_rules}

{output_schema}

EXAMPLES of good hardcoded_artifacts for a value collection:
- Each member is itself a load-bearing identifier — emit them as a
  single id_list artifact (kind="id_list", value=[<sorted members>]).
- Numeric thresholds with domain meaning ("13 ата / 9 ата pressure
  levels") → kind="threshold".
- Domain phrase strings used by templates → kind="phrase".

PROSE GUIDANCE FOR VALUE COLLECTIONS:
- Open with what the collection represents in the domain (single sentence) —
  what kind of entity these values denote.
- State how the collection is consumed: "membership-tested by `is_valid_status`
  in the same module and 4 callers across services", "iterated by the
  template loop in tab_status.html".
- End with the non-trivial detail — a known caller pattern that
  silently breaks on removal: "removing 'paid' breaks invoice.html
  literal text and 18 test assertions", "this enum is JSON-serialized
  to the frontend — adding a value requires an API contract bump".

COLLECTION METADATA + VALUES FOLLOW. Consumer counts and a sample of
caller files are provided so the description can be specific about
the blast radius.
"""


def format_class_prompt(
    class_name: str,
    file_path: str,
    line_start: int,
    line_end: int,
    base_classes: list[str],
    method_signatures: list[str],
    attribute_lines: list[str],
    docstring: str = "",
) -> str:
    """Render the full class_unit prompt.

    `method_signatures` — one-line "def method(params)" entries (no bodies).
    `attribute_lines` — verbatim source lines for each class-body
    attribute assignment (already collected by the class_attrs scanner).
    """
    body = _CLASS_PROMPT.format(
        description_rules=_DESCRIPTION_RULES,
        output_schema=_OUTPUT_SCHEMA,
    )
    parts = [
        body.rstrip(),
        "",
        f"CLASS: {class_name}  ({file_path}:{line_start}-{line_end})",
    ]
    if base_classes:
        parts.append(f"BASES: {', '.join(base_classes)}")
    if docstring:
        parts.append("DOCSTRING:")
        parts.append(docstring.strip())
    if method_signatures:
        parts.append("")
        parts.append("METHODS (signatures only):")
        for sig in method_signatures:
            parts.append(f"  - {sig}")
    if attribute_lines:
        parts.append("")
        parts.append("CLASS-BODY ATTRIBUTES:")
        for ln in attribute_lines:
            parts.append(f"  {ln.strip()}")
    return "\n".join(parts)


def format_attribute_prompt(
    name: str,
    class_name: str,
    file_path: str,
    line: int,
    ctor: str,
    annotation: str,
    source_line: str,
    class_summary: str = "",
) -> str:
    """Render the full attribute_unit prompt."""
    body = _ATTRIBUTE_PROMPT.format(
        description_rules=_DESCRIPTION_RULES,
        output_schema=_OUTPUT_SCHEMA,
    )
    parts = [
        body.rstrip(),
        "",
        f"ATTRIBUTE: {name}  ({file_path}:{line})",
        f"CONSTRUCTOR: {ctor}",
    ]
    if annotation:
        parts.append(f"ANNOTATION: {annotation}")
    if class_summary:
        parts.append(f"OWNING CLASS ({class_name}): {class_summary}")
    parts.append("")
    parts.append("SOURCE:")
    parts.append("```python")
    parts.append(source_line.strip())
    parts.append("```")
    return "\n".join(parts)


def format_value_prompt(
    name: str,
    file_path: str,
    line: int,
    kind: str,
    values: list[str],
    consumer_count: int,
    consumer_files: list[str],
) -> str:
    """Render the full value_unit prompt."""
    body = _VALUE_PROMPT.format(
        description_rules=_DESCRIPTION_RULES,
        output_schema=_OUTPUT_SCHEMA,
    )
    parts = [
        body.rstrip(),
        "",
        f"COLLECTION: {name}  ({file_path}:{line})",
        f"KIND: {kind}",
    ]
    # Show all values up to a sane cap; collections > 64 values are
    # already pruned by the detector.
    sample = values[:32]
    parts.append(f"VALUES ({len(values)} total): {sample!r}")
    if len(values) > len(sample):
        parts.append(f"  (+{len(values) - len(sample)} more elided)")
    parts.append(
        f"CONSUMERS: {consumer_count} function(s) "
        f"across {len(consumer_files)} file(s)"
    )
    if consumer_files:
        parts.append(f"CONSUMER FILES: {', '.join(consumer_files[:8])}")
    return "\n".join(parts)
