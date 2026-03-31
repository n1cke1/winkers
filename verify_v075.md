# Winkers 0.7.5 — Self-Verification Guide

Инструкция для Claude Code: как проверить все новые фичи 0.7.5 на реальном проекте.

## Подготовка

```bash
# 1. Перейди в проект-реципиент (любой проект с Python/TS/JS кодом)
cd /path/to/recipient-project

# 2. Установи winkers из dev-ветки
pip install -e /path/to/winkers[semantic]

# 3. Убедись что ANTHROPIC_API_KEY задан
echo $ANTHROPIC_API_KEY
```

---

## Блок 1: Smart zones

**Что проверяем:** зоны не схлопываются в один "src".

```bash
winkers init --no-semantic
```

Затем вызови MCP tool:
```
orient(include=["map"])
```

**Ожидаемый результат:**
- Если проект имеет структуру `src/pkg/...`, зоны должны быть **не** "src", а подкаталоги пакета (`core`, `api`, `models`, etc.)
- Файлы в корне пакета → зона `"core"`
- Файлы в `tests/` → зона `"tests"`
- Для flat project (файлы сразу в корне) — зоны = имена каталогов первого уровня

**Как сломано:** все файлы в одной зоне `"src"`.

---

## Блок 2: Semantic integration

**Что проверяем:** scope() содержит semantic context, CLAUDE.md имеет dynamic summary.

```bash
winkers init   # с API ключом, дождаться semantic enrichment
```

**Проверка 1 — scope() + semantic:**
```
scope(function="<любая_функция_в_зоне_с_intent>")
```
Ожидай в ответе поле `"semantic"` с `zone_intent` (why + wrong_approach) и `data_flow`.

**Проверка 2 — CLAUDE.md:**
Открой CLAUDE.md проекта. Ожидай блок:
```
<!-- winkers-semantic-start -->
### Project context (auto-generated)

- **Data flow**: ...
- **Domain**: ...
<!-- winkers-semantic-end -->
```

**Как сломано:** scope() без поля `semantic`; CLAUDE.md без dynamic блока.

---

## Блок 3: orient() token budget

**Что проверяем:** при запросе всех секций orient не взрывается по токенам.

```
orient(include=["map","conventions","functions_graph","hotspots","routes","ui_map"])
```

**Ожидаемый результат:**
- Если ответ превышает ~2000 токенов → `"_truncated": true` + `"_hint"` с перечислением пропущенных секций
- Секции идут в порядке приоритета: map первым, functions_graph последним
- Для маленького проекта truncation может не сработать — это нормально

**Проверка с малым бюджетом:**
```
orient(include=["map","functions_graph"], max_tokens=100)
```
Ожидай `_truncated: true`.

**Как сломано:** все секции вернулись без truncation при max_tokens=100.

---

## Блок 4: Improve loop

**Что проверяем:** analyze и improve CLI работают end-to-end.

### Шаг 1: Запись сессии
```bash
winkers record
```
Если нет записанных сессий — это ок, проверяем что команда не падает.

### Шаг 2: Анализ (нужна хотя бы одна сессия)
```bash
winkers analyze
```
**Ожидаемый результат:** "Analyzing: <task>..." → "N insight(s)" → итог "Done. N new insight(s), M open total".
Без сессий: "No recorded sessions."

### Шаг 3: Просмотр insights
```bash
winkers improve
```
**Ожидаемый результат:** список insights с приоритетами (high/medium/low) или "No open insights."

### Шаг 4: Применение (если есть high-priority insights)
```bash
winkers improve --apply
```
**Ожидаемый результат:** "Applied N insight(s) to semantic.json constraints." + backup в `.winkers/history/`.

**Как сломано:** команды падают с трейсбеком; insights не сохраняются в `.winkers/insights.json`.

---

## Блок 5: Protect --startup

**Что проверяем:** обнаружение entry point и трассировка import chain.

```bash
winkers protect --startup
```

**Ожидаемый результат:**
- Автодетект entry point (app.py, main.py, etc.)
- "Startup chain: app.py -> N files protected."
- `cat .winkers/config.json` содержит `"protect": {"mode": "startup", "entry": "...", "chain": [...]}`

Если entry point не найден:
```bash
winkers protect --startup --entry <файл>
```

**Проверка в MCP:**
```
scope(file="<файл_из_chain>")
```
Ожидай: `"startup_chain": true` + `"warning": "This file is in the startup chain..."`

```
orient(include=["map"])
```
Зоны с protected файлами должны содержать `"startup_chain": N`.

**Как сломано:** config.json не создан; scope не показывает warning; orient map без startup_chain.

---

## Блок 6: Doctor + установка

**Что проверяем:** doctor показывает полную диагностику.

```bash
winkers doctor
```

**Ожидаемый результат (все зелёные для настроенного проекта):**
```
  [ok] Python 3.12.x
  [ok] tree-sitter installed
  [ok] All 7 language grammars installed
  [ok] git available
  [ok] anthropic 0.x.x
  [ok] ANTHROPIC_API_KEY set (sk-ant-xxx...)
  [ok] graph.json: N files, M functions, K call edges
  [ok] semantic.json: N zone intents
  [ok] MCP registered (.mcp.json)
  [ok] Schema version: 2

  10 ok, 0 warning(s)
```

**Дополнительно — schema version:**
```bash
python -c "import json; d=json.load(open('.winkers/graph.json')); print(d['meta']['schema_version'])"
```
Ожидай: `2`

**Как сломано:** doctor падает; schema_version отсутствует в graph.json.

---

## Блок 7: Commit format + hooks

**Что проверяем:** hooks install создаёт hook и config.

```bash
winkers hooks --template "[{ticket}] {message} | {author} | {date}"
```

**Ожидаемый результат:**
- `.githooks/prepare-commit-msg` создан
- `.winkers/config.json` содержит `"commit_format": {"template": "...", "ticket_pattern": "..."}`

**Проверка нормализации:**
```bash
winkers commits --range HEAD~3..HEAD
```
Показывает diff между текущими и нормализованными сообщениями (dry-run).

**Активация hook:**
```bash
git config core.hooksPath .githooks
```
Следующий `git commit -m "PROJ-42 fix bug"` → автоформат по шаблону.

**Как сломано:** hook файл не создан; config.json без commit_format; commits падает.

---

## Блок 8: README + версия

**Что проверяем:** версия и документация.

```bash
python -c "import winkers; print(winkers.__version__)"
```
Ожидай: `0.7.5`

```bash
winkers --version
```
Ожидай: `winkers, version 0.7.5`

Проверь что README.md содержит:
- Quick start с `pipx install winkers`
- Таблицу MCP tools (orient, scope, convention_read, rule_read)
- Секцию Privacy
- Improve loop описание

Проверь что CHANGELOG.md существует и содержит секцию `## 0.7.5`.

**Как сломано:** старая версия; README не обновлён.

---

## Полный smoke test (5 минут)

Последовательность для быстрой проверки всех блоков:

```bash
cd /path/to/recipient-project

# 1. Init (блоки 1, 2, 6, 8)
winkers init
# -> зоны не "src" (блок 1)
# -> semantic summary в CLAUDE.md (блок 2)
# -> schema_version: 2 (блок 6)
# -> версия 0.7.5 (блок 8)

# 2. Doctor (блок 6)
winkers doctor

# 3. MCP проверки (блоки 1, 2, 3, 5)
# orient с переполнением:
orient(include=["map","conventions","functions_graph","hotspots","routes","ui_map"])
# scope с semantic:
scope(function="<имя_функции>")

# 4. Protect (блок 5)
winkers protect --startup
scope(file="<файл_из_chain>")

# 5. Hooks (блок 7)
winkers hooks
winkers commits --range HEAD~3..HEAD

# 6. Improve loop (блок 4) — если есть записанные сессии
winkers record
winkers analyze
winkers improve
```

---

## Критерии прохождения

| # | Проверка | Pass |
|---|----------|------|
| 1 | Зоны != "src" для глубокого layout | orient map показывает подкаталоги |
| 2 | scope() содержит `semantic.zone_intent` | поле есть при наличии semantic.json |
| 3 | orient() truncation при max_tokens=100 | `_truncated: true` |
| 4 | `winkers analyze` не падает | exit code 0 |
| 5 | `winkers improve` показывает insights | список или "No open insights" |
| 6 | `winkers protect --startup` создаёт chain | config.json с protect.chain |
| 7 | scope(file=X) для protected файла | `startup_chain: true` |
| 8 | `winkers doctor` без трейсбеков | ok/warning вывод |
| 9 | schema_version=2 в graph.json | meta.schema_version == "2" |
| 10 | `winkers hooks` создаёт .githooks/ | файл prepare-commit-msg |
| 11 | `winkers commits` dry-run работает | exit code 0 |
| 12 | Версия 0.7.5 | `winkers --version` |
| 13 | CLAUDE.md содержит semantic summary | блок `winkers-semantic-start` |
| 14 | README содержит Privacy секцию | grep "Privacy" README.md |
