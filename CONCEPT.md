# Winkers — концепция доработки (post-I9 redesign)

**Started:** 2026-04-29
**Status:** draft, обсуждено в сессии 2026-04-29
**Источники:** [ISSUES_run_I9_observations.md](ISSUES_run_I9_observations.md), [ISSUE_impact_literal_blind.md](ISSUE_impact_literal_blind.md), [TODO.md](TODO.md)

## Мотивация

Run I9 (S-WNK, 2026-04-29) — task passed, но шесть наблюдений показывают системные пробелы (slow post-write, blind index, advisory gates). Run I5 (2026-04-28) — VALUE_LOCKED literal-blind баг, 7/8 catastrophic regress. Текущий гейт-стек (before_create + pre-write + post-write + session_done) построен по принципу «в нескольких местах понемногу» → ни один не enforce'ит, ни один не покрывает trap.

Концепция сворачивает гейты к одной чёткой роли каждого, расширяет покрытие графа, и переезжает на единый семантический проход.

## Принципы

1. **Один гейт, одна ответственность.** Pre-write — только дубликаты. Post-write — корректные warnings (не блок). Stop hook — итоговый audit.
2. **Без агентских петель.** FAIL = информация, не принуждение. Stop всегда clean exit.
3. **Один LLM-проход на unit.** Все consumers (browse / orient / before_create / embeddings / impact) кормятся из одного артефакта.
4. **Coverage расширяется кратно**, не точечно — классы, атрибуты и value collections входят в граф как полноправные unit kinds.
5. **Прозрачность поверх всего.** `hooks.log` JSONL — фундамент, без него ничего не измерить.

---

## 1. Coverage: class, attribute и value units

### Проблема

Сегодня граф/индекс содержит только функции (`function_unit`, `traceability_unit`). SQLAlchemy `relationship`, Pydantic `Field`, dataclass поля, React props не индексируются — `find_work_area` их не находит, resolver `before_create` не разрешает (Issue 1, Issue 4 в I9). Параллельно `value_locked.py` детектит коллекции литералов, но они не попадают в общую модель units (хранятся отдельно в `graph.value_locked_collections[]`).

### Решение

Расширить `unit.kind` до четырёх вариантов:

| kind | что включает | source |
|------|--------------|--------|
| `function_unit` | функции, методы | существующие tree-sitter queries |
| `class_unit` | определения классов | новые queries: `class_definition` |
| `attribute_unit` | class-body assignments с вызовами (`relationship(...)`, `Field(...)`, `Mapped[...]`) | новые queries: `(annotated_)assignment` с RHS=`call` |
| `value_unit` | module-level literal collections (`set`/`frozenset`/`dict` literals) | [src/winkers/value_locked.py](src/winkers/value_locked.py) (existing detector, expand cross-file) |

Schema bump в `unit.kind` consumers (~5–10 мест), новые tree-sitter queries в каждом language profile (Python приоритет; JS/TS, Java, Go, Rust, C# — потом).

### Resolver upgrade (Issue 4)

В [src/winkers/target_resolution.py](src/winkers/target_resolution.py) добавить:

- `_CLASS_ATTR_RE = r"\b([A-Z]\w*)\.([a-z]\w*)\b(?!\s*\()"` — `Class.attr` без скобок
- comma/`and`-разделённые батчи (`fix Client.invoices, Client.payments`) — работает автоматически после первого пункта
- structured-hint fallback в `_before_create_unknown`: вместо плоского `error` — «попробуй `Class.method()` или `Class.attribute`»

Resolver upgrade и graph coverage шипятся **связкой**: regex без `attribute_unit` в графе = резолвит в пустоту.

---

## 2. Hardcoded relationships taxonomy

В проекте есть три ортогональных класса хардкоженных взаимосвязей. Каждый ложится на свой артефакт; смешивать их в одном детекторе — корень I5 trap'a.

| Kind | Примеры | Артефакт | Детектор |
|------|---------|----------|----------|
| **Value collections** | `VALID_STATUSES = {"draft","sent","paid"}`, enums-as-sets | `value_unit` записи в `units.json` | [value_locked.py](src/winkers/value_locked.py) (existing, expand cross-file) |
| **String-literal cross-file uses** | `Invoice.status == "sent"`, `if x in {"paid","void"}`, fixture data | `.winkers/expressions.json` (новый артефакт) | новый AST visitor на `winkers init`, syntactic-context aware |
| **Coherence invariants** | "когда X.py меняется, Y.py должен следовать", schema↔migration sync | `project.json::rules` с `sync_with`, `fix_approach` (existing) | LLM audit + auto-detectors (existing) |

### `value_unit` — 4-й kind в `units.json`

Та же single-semantic-pass схема, что у остальных units (`summary`, optional `description`, `risk_level`, `dangerous_operations`). Детектор остаётся [value_locked.py](src/winkers/value_locked.py), но расширяется:

- **Path 4 / Gap 2 из ISSUE_impact_literal_blind**: cross-file consumers через walk `graph.import_edges`, не только same-file. Сегодня `_find_referencing_fns` ([value_locked.py:154–177](src/winkers/value_locked.py#L154)) ограничен `file_node.function_ids` — расширяется на консьюмеров через import-граф.
- **LLM description опционально**: для нетривиальных коллекций («canonical invoice statuses; removing breaks tests, repos, frontend payloads») — флаг `--no-value-unit-llm` отключает LLM-проход для cost-чувствительных кейсов.

Бенефит: коллекции становятся discoverable через `orient(task=...)` — embedding query «status enum» вернёт `VALID_STATUSES` в `semantic_matches`. Сегодня этого нет.

### `.winkers/expressions.json` — новый артефакт

Path 2 из ISSUE_impact_literal_blind, формализованный как параллельный артефакт `units.json`:

```json
{
  "values": {
    "sent": [
      {"file": "tests/test_invoice.py", "line": 42,
       "kind": "comparison | call_arg | dict_value | subscript | match",
       "context": "..."},
      ...
    ]
  },
  "content_hash": "..."
}
```

Строится AST visitor'ом на `winkers init`. Кэшируется по mtime+hash как `descriptions/`. **Scope: matched-only** — литерал должен встречаться в `value` какого-то `value_unit.values`, иначе не индексируется. Frequency threshold ≥3 (хранится только если ≥3 use sites) — bound size индекса на больших repo.

`diff_collections` ([value_locked.py:244–284](src/winkers/value_locked.py#L244)) консультируется с `expressions.json` для корректного VALUE_LOCKED счёта. «0 caller literal use(s) at risk» из I5 trap'a превращается в «47 string-literal occurrences in 18 files (comparison/call_arg/subscript)».

### Coherence rules → `project.json::rules`

Без изменений. Auto-detected coherence flagged with `auto_detected: true`, юзер accept/reject — flow существует. **Не** промоутятся в units — это project-level invariants, не per-symbol records.

---

## 3. Single semantic pass → `.winkers/units.json`

### Проблема

Сегодня три параллельных LLM-прохода на функцию:
- intent generation → `graph.json::FunctionNode.intent`
- description generation → `descriptions/` cache → embedding input
- impact analysis → `impact.json` (combined с intent в одном вызове после 0.8.x)

Дубликат работы; разные поля, разные кэши; consumer'ы лезут в три места.

### Решение

Один LLM-вызов на unit, единый артефакт `.winkers/units.json`:

```json
{
  "units": {
    "<unit_id>": {
      "kind": "function_unit | class_unit | attribute_unit | value_unit",
      "summary": "<one-liner — для browse, orient (legacy `graph.intent` field)>",
      "description": "<paragraph — для before_create context, embeddings input>",
      "secondary_intents": ["..."],
      "risk_level": "low|medium|high|critical",
      "dangerous_operations": ["..."],
      "safe_operations": ["..."],
      "callers_classification": {...},
      "content_hash": "..."
    }
  }
}
```

### File model

Three-file модель консолидируется в **three-file** (плюс session):

- `graph.json` — facts (files, functions, classes, attrs, edges, value collections); ссылается на units по `unit_id`
- `units.json` — per-unit semantics (заменяет `graph.intent`, `descriptions/` cache, `impact.json` целиком)
- `project.json` — project-level (см. ниже)
- `.winkers/sessions/<id>/` — ephemeral per-session state

`project.json` consolidates project-level semantics + rules в одну сущность:

```json
{
  "semantic": { "data_flow": "...", "domain_context": "...",
                "zones": {...}, "monster_files": [...],
                "new_feature_checklist": [...] },
  "rules":    { "<category>": [{title, wrong_approach, severity,
                                 fix_approach, sync_with, stats,
                                 user_accepted}] }
}
```

Rationale for merge: оба project-level, оба LLM-generated на init, оба читаются `orient` (`conventions` / `rules_list` includes). `convention_read` MCP-tool уже сегодня тянет из `semantic.json` (data_flow, domain_context, checklist), а не из rules — naming уже было непоследовательным. User curation (accept/reject rules) сохраняется через `user_accepted` флаг внутри merged-файла; partial regen мерджит по section keys.

`impact.json` упраздняется (его поля переезжают в units). `expressions.json` живёт параллельно (см. секцию 2).

### Длина description: kind-specific soft budget

Без hard cap. LLM генерирует то, что нужно kind'у:

| kind | summary | description |
|------|---------|-------------|
| `function_unit` | ~10w | ~80–150w |
| `class_unit` | ~15w | ~150–250w (state, lifecycle, инварианты по методам) |
| `attribute_unit` | ~5w | ~20–50w (`relationship("Contract", back_populates="client", cascade="all,delete")` + роль) |
| `value_unit` | ~10w | ~30–80w (опционально; «canonical invoice statuses; removing breaks repos+tests+payloads») |

Soft target в промпте, kind-specific guidance.

### Embedding input

Сейчас `name + description` идёт в индекс ([_embed_text_for](src/winkers/embeddings/builder.py#L266) в `embeddings/builder.py`). Description тяжелее (до 250w для class) — BGE-M3 ест до 8K токенов, retrieval должен **выиграть** за счёт плотности сигнала. Регрессия проверяется на 15-query battery (см. Issue 1 в ISSUES_run_I9_observations.md) до/после.

### Стоимость

Repo на 400 функций: было 1200 LLM-вызовов (3×400), станет ~400. Cache по `content_hash` уже есть в impact, переносится на единый артефакт. С `class_unit` + `attribute_unit` + `value_unit` общее число units растёт (с 400 до ~700–800), но всё равно ниже текущих 1200.

---

## 4. `orient(task)` — mandatory + merge `find_work_area`

### Проблема

Старт сессии = `orient` → агент думает → `find_work_area`. Два tool-вызова там, где у агента уже на первом ходе есть полное описание задачи.

### Решение

```
orient(include=[...], task: str)
```

- `task` **required** — обязательный free-text task description
- Возвращает стандартный map / conventions / rules_list **плюс** `semantic_matches: [{unit_id, score, summary, snippet}]` (top-K из embeddings)
- Bounded-wait прелоада embeddings переезжает сюда (15s timeout)
- `find_work_area` остаётся как deprecated alias на один минор для backwards compat, потом удаляется

### Edge case: «нет конкретного task пока»

Бывает: «explore the repo first», «what does this code do», первое знакомство с проектом. `task` должен быть **честным**, не идеальным — расплывчатые task'и просто дают шумные `semantic_matches` и (вероятно) WARN в post-session audit, не trap.

- `task: "explore project structure"` — валидно, embeddings вернут разнообразный topK
- `task: "<task statement as-is>"` — копия исходного задания, тоже валидно

### Проверено в коде

Pipeline'ы независимые сегодня: `find_work_area(query=...)` ([tools.py:263](src/winkers/mcp/tools.py#L263)) и `before_create(intent=...)` ([tools.py:150](src/winkers/mcp/tools.py#L150)) используют разные параметры и decoupled код-пути (нет общего парсинга). Schema rename `query → task` — чисто механическая правка.

---

## 5. Vocabulary

| Param | Где | Granularity | Purpose | Audit axis |
|-------|-----|-------------|---------|------------|
| `task` | `orient` | task-level (зоны, тема) | retrieval / discovery | task fulfillment (затронут ли scope task'a) |
| `intent` | `before_create` | unit-level (`Class.method`, `file::fn`) | risk gate, blast radius | intent fulfillment (затронуты ли registered targets) |

`task` течёт сверху (от пользователя/upstream), `intent` течёт снизу (от выбранного агентом действия). `task` ставится **один раз на сессию**, `intent` — **per concrete change**, многократно.

---

## 6. Intent formation rules

Структура полезного task / intent:

| Component | Required? | Example |
|-----------|-----------|---------|
| Verb-first | required | `create` / `change` / `fix` / `add` / `refactor` / `extract` / `remove` / `rename` / `audit` |
| Target если применимо | для `intent`: strongly preferred; для `task`: optional | `Class.method()`, `Class.attr`, `file.py::fn`, path |
| Goal в одну фразу | required | что должно стать, не как сейчас |
| One concern | required | без `and` / `&` / multi-task lists |

### ✅ Good

- `simplify invoice statuses from 6 to 3` — verb + scope + concrete change
- `fix Client.invoices relationship cascade` — verb + Class.attr
- `add soft-delete to all financial repos` — verb + zone
- `extract date utilities from app/services/billing.py` — verb + path
- `audit soft-delete consistency across repos` — verb + scope

### ❌ Bad

- `improve invoice handling` — нет verb с конкретикой, нет scope
- `invoices` / `statuses` — bare noun
- `fix bug X and add feature Y` — multi-task, audit intent fulfillment не сможет сравнить
- `rewrite using Pydantic v2` — implementation-first, goal lost
- `make it better` / `refactor everything` — без таргета и без verb-конкретики

### Где это enforce'ится

- **CLAUDE.md** — instructional reference для агентов
- **`orient` soft validation** (warning, не блок):
  - `task.split() < 3 words` → «task очень короткий, рекомендуется добавить verb + scope»
  - regex `\b(and|&)\b` плюс ≥2 verb-like tokens → «task выглядит multi-task; разделите на несколько сессий»
  - zero `semantic_matches` со score > 0.5 → «task не нашёл релевантной зоны; перефразируйте с named target»
- **Post-session audit FAIL diagnosis** — при FAIL по «intent → 0 overlap» добавляется intent quality summary («intent был 2 слова, no targets matched in graph») — превращает FAIL из загадки в диагноз
- **Stats в audit.json** — собирается intent quality vs PASS/FAIL ratio за N сессий → data-driven гайд

---

## 7. Hooks observability (P0, Wave 1)

### Проблема

`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop` ничего не логируют. Нельзя измерить overhead, нельзя отдебажить debounce, audit или literal-blind фикс (Issue 3).

### Решение

Per-session папка `.winkers/sessions/<id>/`. Внутри:

- `hooks.log` — append-only JSONL, каждый хук пишет одну строку
- `intents.json` — registered before_create calls (для audit intent fulfillment)
- `seen_units.json` — context dedup (см. секцию 9)
- `audit.json` — итоговый audit от Stop hook (см. секцию 8)

Каждый хук оборачивает `run()` в `try/finally` recorder:

```json
{"ts": "2026-04-29T10:23:14.521Z", "session_id": "...",
 "event": "PostToolUse", "hook": "post_write",
 "file": "app/repos/invoice.py",
 "duration_ms": 1240, "outcome": "ok",
 "warnings_emitted": 1, "tool_call_idx": 7}
```

Sub-day implementation. **Идёт первым, до всего остального** — без него мы пишем debounce/audit/literal-blind вслепую.

### GC per-session папок

TTL 7 дней по mtime, чистка на `winkers init` или при превышении ~50 папок. Не блокирующая операция.

---

## 8. Post-session audit с 3-tier verdict

### Принципы

- **Без петель.** Stop hook всегда clean exit, никаких `exit_code:2` / `decision:"block"`. FAIL чисто информационный.
- **Точность важнее полноты.** Один false-positive FAIL = агент учится игнорировать. Критерии консервативные.

### Тиры

```
PASS  — task/intent выполнены, правил не нарушено, импорты целы
WARN  — scope creep / minor rule miss / unregistered intent
FAIL  — структурный провал по жёстким критериям
```

### Критерии FAIL (high precision)

| Критерий | Почему жёсткий |
|----------|----------------|
| Broken imports / syntax errors в финальном диффе | объективно сломано |
| Intent зарегистрирован → 0 overlap с диффом | заявленная работа не сделана (intent fulfillment axis) |
| VALUE_LOCKED с literal_hits > N в финальном reconciled audit, не упомянут в session_done | I5-класс trap, основной кейс |
| Coherence rule violation, помеченная в `project.json::rules` как `severity: blocking` | агент сам опт-ин такие правила пометил |

### Критерии WARN (не FAIL)

- Scope creep (изменены файлы вне blast radius), но intent выполнен
- Style-rule miss (длина функции, naming)
- Intent вообще не зарегистрирован — это терра инкогнита, выбор агента
- Один-два VALUE_LOCKED hits ниже порога

### Каналы вердикта

| Канал | Семантика | Action |
|-------|-----------|--------|
| `session_done` (агент сам) | до Stop, есть шанс пофиксить | self-correction loop, опционально |
| Stop hook → `audit.json` | пост-фактум, агент уже закончил | телеметрия, бенч-сигнал |
| `prompt_enrich` следующей сессии | если предыдущая FAIL — подсасывается | continuation context |

### Bonus для бенча

Второй ортогональный axis к тестовому: «winkers по своим критериям FAIL» vs «тесты красные». Расхождение диагностически ценно:
- high tests-pass + winkers-FAIL → недо-покрытие тестов
- low tests-pass + winkers-PASS → literal-blind / coverage gap в самом winkers (как I5 сегодня)

---

## 9. Context dedup: `seen_units`

### Принцип

Если `orient(task=...)` вернул description'ы, а `before_create` потом включает те же units — повторно полный description не возвращаем, оставляем маркер.

### Mechanism

Per-session `seen_units.json`:

```json
{
  "<unit_id>": {
    "tool": "orient",
    "fields_returned": ["summary", "description", "risk_level"],
    "ts": "...",
    "tool_call_idx": 7
  }
}
```

Tools, которые **пишут** в `seen_units`:

| Tool | Что отдаёт description |
|------|------------------------|
| `orient(task=...)` | top-K semantic_matches |
| `find_work_area` (deprecated) | top-K |
| `scope` | целевой unit + callers/callees |
| `before_create` | affected_fns + similar_logic |
| `browse` | **нет** (только summary) |

### Suppression

`before_create` перед ответом для каждого unit:

```
ЕСЛИ unit_id в seen_units И (current_call_idx - seen_call_idx) < THRESHOLD
  → { unit_id, summary, description_seen_in: "orient@call#7" }
ИНАЧЕ
  → полный description + остальные поля
```

`THRESHOLD = 10` tool-вызовов default. Защита от context compaction: за 10 вызовов оригинальный description мог уехать из контекста, тогда возвращаем заново.

Подавляется **только description** (тяжёлое поле). `summary`, `risk_level`, `callers_classification` — всегда полные.

---

## 10. Что отброшено явно

- **Pre-write enforcement** (intent gate с lookup в registry, locked-fn-overlap detection, `skip_analysis=true` бэкдор) — отброшено в пользу post-session audit. Pre-write остаётся тонким (только AST-hash duplicate detection).
- **Mid-session reverse-engineer intent из диффа** на Stop hook — дорого и шумно.
- **Block-on-accumulated-warnings** (Path 3 из ISSUE_impact_literal_blind) до literal-blind фикса — блокировать на шумном гейте хуже, чем не блокировать.
- **Optional `task` для `orient`** — отброшено в пользу mandatory. Rationale: rare optimization, strictly worse чем передать литерал task statement.

---

## Зависимости и волны

```
Issue 3 (hooks.log)            ──▶ всё остальное
                                   ▲ без него ничего не измерить

Issue 6 (post-write debounce)  ──▶ Path 1 literal-blind
                                   ▲ post-write становится главным гейтом,
                                     обязан быть быстрым + корректным

Point 4 (orient(task) merge)   — независим
Point 3 (units.json) +          ─┐
Issue 4 (resolver attrs) +      ─┼─▶ связка, шипятся вместе
class/attribute/value units      ─┘    (resolver бесполезен без graph coverage;
                                        value_unit без cross-file fix теряет смысл)

Post-session audit (Point 8)   ──▶ требует hooks.log + session registry
                                     (intents.json, seen_units.json)

Context dedup (Point 9)        ──▶ требует session registry
                                     (та же папка, другой файл)
```

### Wave 1 — observability (sub-day)

- Issue 3: `hooks.log` JSONL
- Per-session папка `.winkers/sessions/<id>/`
- Schema-заглушки под `audit.json` / `intents.json` / `seen_units.json`

### Wave 2 — small UX wins (≤1 день каждое, параллельно)

- Point 4: `orient(task)` mandatory + rename + deprecation `find_work_area`
- Intent formation rules в CLAUDE.md + `orient` soft-validation warnings
- Issue 4 first half: regex для `Class.attr` (без graph coverage пока — частичный выигрыш на function_units)
- Issue 2: language detection at init, lock в config

### Wave 3 — performance + correctness gate (последовательно)

- Issue 6: post-write debounce (content-hash skip + burst coalesce)
- Path 1 literal-blind: scoped grep с allowlist расширений (sub-day)
- Word-clarification: `caller literal use(s)` → `call-site literal use(s)`

### Wave 3.5 — cross-file consumers fix

- Gap 2 / Path 4 из ISSUE_impact_literal_blind: `_find_referencing_fns` ([value_locked.py:154](src/winkers/value_locked.py#L154)) расширяется на cross-file consumers через walk `graph.import_edges`
- Подготовка к value_unit promotion (Wave 5b)

### Wave 4 — single semantic pass (неделя+)

- Point 3: единый LLM-вызов, kind-specific budget
- Schema migration: `graph.intent` + `descriptions/` + `impact.json` → `units.json`
- Schema migration: `semantic.json` + `rules.json` → `project.json` (merge by section keys, preserve `user_accepted`)
- `value_unit` joins as 4-й kind в single-pass schema (description опциональный)
- Cache rebuild на `content_hash`

### Wave 5a — class + attribute coverage (неделя+, после Wave 4)

- `class_unit` + `attribute_unit` в tree-sitter queries (Python первый)
- Resolver second half: `attribute_unit` resolution
- Embeddings reindex
- Quality regression test (15-query battery)

### Wave 5b — value_unit promotion + expressions.json

- `value_unit` records в units.json (детектор уже есть после Wave 3.5)
- AST visitor для `expressions.json` — Path 2 из ISSUE_impact_literal_blind
- Заменяет grep из Wave 3 для Python (grep остаётся fallback'ом для non-Python)
- `diff_collections` переходит на `expressions.json` для корректного VALUE_LOCKED

### Wave 6 — post-session audit (после Wave 1+3+4)

- Point 8: Stop hook → `audit.json`
- 3-tier verdict, критерии
- `session_done` → читает + дополняет audit
- `prompt_enrich` подсасывает FAIL audit на новой сессии

### Wave 7 — context dedup (после Wave 1)

- Point 9: `seen_units.json` + suppression в `before_create`
- Можно параллельно с Wave 4–6, не блокирует

---

## Открытые вопросы

- **Per-session папка GC**: TTL 7 дней? По числу папок? На каждый `winkers init`?
- **Class lock_status**: что значит «locked» для класса? Есть subclass'ы? Используется в isinstance/import? Нужна отдельная классификация.
- **Attribute lock_status**: callers нет в традиционном смысле — есть consumers (код, который читает `client.invoices`). Отдельная классификация для attr-юнитов.
- **Stop hook на benchmark adapter**: есть гарантия, что Stop вызовется до завершения процесса? Если нет — `audit.json` может не успеть записаться, тогда нужен fallback на `atexit` в MCP-сервере.
- **THRESHOLD для seen_units suppression**: 10 tool-вызовов — наугад. После Wave 1 + bench-данные пересмотрим.
- **`orient` task validation thresholds**: 3 words / multi-task regex / score>0.5 — defaults proposed, pending bench data.
- **Backwards compat `orient(intent=)`**: принимать ли старое имя как alias на один минор для существующих скриптов/агентов?
- **`task` vs `intent` audit divergence**: когда task говорит «audit soft-delete», а отдельные `intent`'ы — «fix Client.invoices», что считать task fulfillment? Intersection of intent fulfillments? Diff coverage по task-named scope? Open для Wave 6 design.
- **`value_unit` LLM description default**: on by default vs off? Cost ~$0.01 × ~30 collections = ~$0.30 на init большого repo. Lean на on, флаг `--no-value-unit-llm` для cost-чувствительных.
- **`expressions.json` scope policy**: matched-only (literal в каком-то `value_unit.values`) с frequency ≥3 vs all literals. Default matched+threshold; replay на бенч-репо чтобы измерить index size перед locking policy.
- **Non-Python literal sources** (SQL/JSON/HTML/Jinja): grep fallback (Wave 3 Path 1) vs расширение AST индекса per-language (Wave 5b extension). Default grep, AST per-language по запросу.
