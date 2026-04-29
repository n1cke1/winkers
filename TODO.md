# Winkers — follow-ups (started 2026-04-26)

Pickup-point для следующей сессии. Сегодня закрыто: P0 (#1-3), плюс tespy-side фиксы (auto-update script + restore коммита).

## ~~P0 — критичный баг в `winkers init`~~ ✅ DONE 2026-04-26

Закрыто коммитом [`600cfb9`](src/winkers/cli/main.py): `fix(init): absolute winkers path everywhere; stop deleting user-scope MCP`. Три бага в одном:

1. **`.mcp.json` writes `command: "uvx"`** → новый `_winkers_bin()` helper (venv → sys.argv[0] → PATH → bare); пишется абсолютный путь
2. **Hooks writes bare `winkers`** → тот же helper feed'ит `_install_session_hook`; все 7 хуков с абсолютным путём
3. **Init deletes user-scope MCP** → переименован `_remove_user_scope_mcp` → `_migrate_user_scope_mcp`: refresh path/args если запись есть, no-op если нет

## P1 — функциональность

### 4. Step 2 в CLAUDE.md sniper-bug Claude Code 2.1.x
Описано в фидбеке invoicekit от 2026-04-26 (Issue #4).

Любая правка строки `2. ...` в шаблонном workflow → claude --print виснет на старте сессии (0 turns / $0 / outer timeout).

**Гипотеза:** что-то в Claude Code зависит от конкретной структуры/маркера. Не воспроизвели у себя (наш Linux-стенд — другие условия).

**Фикс на нашей стороне (опционально):** реструктурировать сниппет — выкинуть numbered list, использовать отдельные `## Step: orient` секции. Раньше отложили до проверки на нашей машине.

## P2 — хорошо бы

### 5. Ticket-runner на tespy не закоммичен
Файл: [/opt/tespy-chp-web/patches/ticket_service_runner.py](/opt/tespy-chp-web/patches/ticket_service_runner.py).

Patch для Linux лежит в репо но требует ручного `cp` в site-packages. Сейчас в site-packages версия с моими правками (no-Windows + explicit MCP allowlist + ToolSearch). При следующем `pip install` (если меняется requirements.txt) ticket-service переустановится и снесёт patch.

**Что уже сделано сегодня (частично):**
- `tespy-chp-update.sh` больше не делает `git reset --hard` (новая matrix-логика, см. /usr/local/bin/tespy-chp-update.sh) — local commits НЕ уничтожаются
- Patch с runner.py + explicit MCP allowlist `Read,Write,Edit,Glob,Grep,Bash,ToolSearch,mcp__winkers__*` (8 имён explicitly) лежит в site-packages

**Что осталось:**
- Закоммитить **исправленную** Linux-версию `patches/ticket_service_runner.py` в master tespy (сейчас в master лежит старая Windows-версия из e46109e). Тогда `cp` шаг можно автоматизировать.
- Добавить в `/usr/local/bin/tespy-chp-update.sh` шаг `cp $REPO/patches/ticket_service_runner.py $REPO/.venv/lib/python3.12/site-packages/ticket_service/runner.py` после `pip install`. Чтобы pip-reinstall не побеждал.

Это уже задача tespy, не winkers — но влияет на работу винкерс в проде.

### 6. CPU-only torch для `pipx install winkers`
Сейчас pin через `[tool.uv.sources]` работает только для `uv`/`uvx`. `pipx` (рекомендуемая команда из README) использует pip и игнорирует pin → 5GiB CUDA bloat.

**Возможное решение:** оптимизация требует либо отдельной инструкции в `pipx install --pip-args`, либо переключения README на `uv tool install`. README уже описывает workaround, но primary install command (`pipx install winkers`) всё ещё опасна.

## Pickup для следующей сессии (после 2026-04-26 afternoon)

### Состояние оба репо

- `/root/winkers` HEAD `a393827` — **запушен на origin/main** (10 коммитов с `ee58acf`).
- `/opt/tespy-chp-web` HEAD `6e7bafa` — **запушен на origin/master**.
- VPS swap расширен 1 → 4 ГБ (после BGE OOM); `/swapfile` + `/swapfile2`, оба в `/etc/fstab`.

### Что работает в проде (подтверждено end-to-end)

- `--setting-sources project` в `runner.py` — хуки наконец грузятся в `claude --print`
- post-write хук обновляет `graph.json` (file-hash skip → 0.4с на повторе, 4-5с на реальной правке, dedup edges 0)
- bounded-wait в `find_work_area` — реально отрабатывает в окне 15с (тест 17:16: BGE warmup, semantic match за 15.5с, score 0.675)
- `action=ask` flow — Haiku задаёт уточняющие вопросы, фронт показывает шаблон, юзер заполняет (тикет T-D9A902)
- `TICKET_PARSE_SYSTEM` без file-list — Haiku больше не направляет агента в удалённые файлы

## ~~🔥 ONNX+INT8 BGE-M3~~ ✅ DONE 2026-04-26 (afternoon, 2nd half)

Готовый ONNX-INT8 от `Xenova/bge-m3` (`sentence_transformers_int8.onnx`,
568 MB, CLS+L2 встроены в граф) — Step 2 (квантизация) пропущен.
[builder.py](src/winkers/embeddings/builder.py) переписан на ORT +
`tokenizers` (без transformers/torch в core); `WINKERS_USE_LEGACY_ST=1`
включает старый float32 fallback. Indices пересобраны на tespy
(417 units, 228s, embeddings.npz.bak-st-float32 в качестве отката).

**Эффект (replace 1.7 GiB float32 stack):**
- cold load 10–15s → 3s
- warm batch encode 5s → 0.1s
- query latency 397ms → 38ms (10× faster)
- resident RAM 1.7 GiB → 1.1 GiB
- disk weights 2.27 GB → 568 MB

**Quality regression** (15 representative queries, 417-unit tespy index):
top-1 match 73% (target был 90%), top-5 overlap 81%, avg score drift 2.4%.
Все 4 mismatch'а в "ambiguous" зонах — другая модель теряет тот же файл/домен.
Sufficient для top-K agent workflow; if regression matters, switch to
`model_fp16.onnx` (1.13 GiB) или legacy ST.

См. CHANGELOG 0.8.4.

**Зачем:** на 2 ГБ VPS текущий stack BGE-M3 в float32 (1.7 ГБ RAM, 10-15с cold load) живёт впритык. ONNX+INT8 даёт 3× по памяти и 2-3× по скорости — снимает почти всю текущую боль.

**Ожидаемый эффект:**
| Метрика | Сейчас | После ONNX+INT8 |
|---------|--------|------|
| RAM resident | ~1.7 ГБ | ~600 МБ |
| Cold load | 10-15с | 3-5с |
| Query latency (CPU) | 1-2с | 0.3-0.5с |
| Disk weights | 2.27 ГБ | ~570 МБ |
| Quality (retrieval top-1) | baseline | -0.5...-2% |

**Почему именно сейчас:** все остальные фиксы (bounded-wait, swap, dedup, file-hash) лечат симптомы. ONNX+INT8 убирает корень — RAM cost. После него bounded-wait почти всегда укладывается в 3-4с вместо 15с.

### Шаги (по порядку):

**1. Получить ONNX-модель** (час):
- Попробовать готовый `Xenova/bge-m3` или `BAAI/bge-m3` ONNX-экспорт с HuggingFace
- Если нет качественного — экспортировать самому: `optimum-cli export onnx --model BAAI/bge-m3 --task feature-extraction --opset 17 ./bge-m3-onnx`
- Проверить размер выхода `model.onnx` (~2.3 ГБ float32) и наличие `tokenizer.json`/`sentencepiece.bpe.model`

**2. INT8 динамическая квантизация** (полчаса):
```python
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic("bge-m3-onnx/model.onnx", "bge-m3-onnx/model_int8.onnx",
                 weight_type=QuantType.QInt8)
```
Без калибровочных данных — для embeddings обычно хватает.

**3. Заменить loader в [src/winkers/embeddings/builder.py](src/winkers/embeddings/builder.py)** (1-2 часа):
- `_get_model()` → создаёт `onnxruntime.InferenceSession(model_int8.onnx, providers=['CPUExecutionProvider'])`
- Tokenizer: `from transformers import AutoTokenizer; AutoTokenizer.from_pretrained("BAAI/bge-m3")` — работает с ONNX-сессией параллельно
- `encode(texts)` → tokenize → ort_session.run → mean-pool по token mask → L2-normalize. Возвращать numpy.float32 (1024-dim) — индексный формат не меняется
- `preload_model()` логика остаётся — просто ORT-сессия + warmup encode
- `wait_for_preload()` остаётся как есть

**4. Управление весами:**
- Зарегистрировать модель как `winkers-bge-m3-int8` в HF Hub под своим аккаунтом, или просто положить в `/root/.cache/huggingface/hub/` локально
- `embeddings/__init__.py` — добавить опцию `WINKERS_USE_ONNX=1` env (по умолчанию ON), fallback на старый path для совместимости
- requirements: добавить `onnxruntime>=1.16` (CPU вариант, ~50МБ) и `optimum` (опционально, для конвертации). Убрать `torch` + `sentence-transformers` из core deps в `[onnx]` extra

**5. Quality regression test** (час):
- Берём 10-20 known-good queries из реальных тикетов (например, "функция расчёта помесячных нагрузок" → должна вернуть `calc_monthly_loads`)
- Сравниваем top-1 + top-5 score float32 vs INT8
- Acceptance: top-1 совпадает в ≥18/20 кейсов, средний score падает ≤5%

**6. Перебилд индекса на tespy:**
- `winkers init --with-units --force-units` пересоберёт `embeddings.npz` с новой моделью
- Проверить что `find_work_area` отвечает быстро после холодного `winkers serve`

**7. Документация:**
- Обновить README "Semantic search (experimental)" — снять disclaimer про 1.7 ГБ, заменить на "~600 МБ INT8" и "~5с cold load"
- CLAUDE.md — обновить описание `embeddings/` модуля
- CHANGELOG entry

### Open questions перед стартом

- **HF community ONNX export для BGE-M3 — есть ли качественный?** Если есть готовый — экономим день. Если нет — придётся самому экспортировать (1-2 часа+ на конвертацию + sanity check).
- **Sparse + multi-vector heads** — мы их не используем (только dense), стандартный ONNX-экспорт = только dense. Нужно убедиться при экспорте что dense берётся.
- **Backwards-compat:** держать ли `WINKERS_USE_ONNX=0` fallback для пользователей winkers без ONNX? Я бы оставил на первый релиз — убрать в следующей мажорной.

### Альтернативные опции если ONNX+INT8 не выйдет (плохое качество / экспорт ломается)

- **`bge-small-multilingual` + INT8** — но quality drop по русскому раньше был неприемлем
- **Cohere Embed v3 multilingual API** — отличное качество, но ломает local-only design + добавляет API key зависимость
- **Гибрид: ONNX BGE-M3 для долгих запросов, FTS5/before_create для коротких** — сложнее, не очевиден выигрыш

## Прочее (P2/P3)

### Auto-cp patches/ в venv
P2 #5 наполовину сделано: `patches/ticket_service_runner.py` + `patches/ticket_service_routes.py` запушены в master tespy. Осталось — добавить в `/usr/local/bin/tespy-chp-update.sh` шаг `cp $REPO/patches/*.py $REPO/.venv/lib/python3.12/site-packages/ticket_service/` после pip install. Без этого pip-reinstall (когда меняется requirements.txt) снесёт `--setting-sources project` и всю action=ask логику.

### P1 #4 Step 2 toxicity
Осталось из утра — не воспроизводили на Linux. Низкий приоритет, фронт-эффект минимальный.

### CPU-only torch для pipx install
P2 #6 — после ONNX+INT8 эта проблема исчезает: torch вообще не понадобится в core (только в `[onnx]`-extra для конвертации, или вообще убрать в отдельный `winkers-tools-onnx-export` репо).
