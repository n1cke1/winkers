# InvoiceKit — Spec для генерации тестового проекта

## Назначение

Тестовый проект для graph-mcp-bench. Генерируется Claude Code "с нуля" (vibe coding).
Результат: рабочее приложение ~100 Python файлов с чёткими архитектурными слоями,
богатой бизнес-логикой и неочевидными конвенциями. Именно на таком проекте
semantic layer даёт максимальный эффект.

---

## Продукт

**InvoiceKit** — внутренняя billing-система для B2B компании.
Клиенты, контракты, инвойсы, позиции, платежи, налоги, отчёты.

Пользователи: бухгалтер, менеджер, admin.
Deployment: self-hosted, single-tenant.

---

## Архитектурные слои

```
frontend/          — Jinja2 templates + HTMX (не SPA, server-side rendering)
  templates/       — HTML templates по разделам
  static/          — CSS, minimal JS

app/
  api/             — FastAPI routes, request/response handling
    routes/        — по доменам: invoices.py, clients.py, payments.py...
    deps.py        — dependency injection (get_db, get_current_user)
    
  services/        — бизнес-логика, orchestration
    invoice.py     — создание, отправка, отмена инвойсов
    payment.py     — приём платежей, allocation
    pricing.py     — расчёт цен, скидок, налогов
    reporting.py   — генерация отчётов
    pdf.py         — PDF генерация инвойсов
    email.py       — отправка уведомлений
    
  domain/          — бизнес-правила, чистые функции
    tax.py         — налоговые расчёты
    discount.py    — правила скидок
    currency.py    — валютные конвертации
    validators.py  — бизнес-валидация (не Pydantic schema)
    status.py      — state machine для invoice lifecycle
    
  models/          — SQLAlchemy ORM models
    client.py
    contract.py
    invoice.py
    line_item.py
    payment.py
    tax_rate.py
    user.py
    audit_log.py
    
  schemas/         — Pydantic schemas (API request/response)
    client.py
    invoice.py
    payment.py
    report.py
    
  repos/           — data access layer (repository pattern)
    base.py
    client.py
    invoice.py
    payment.py
    
  core/            — infrastructure
    config.py      — settings from env
    database.py    — SQLAlchemy engine, session
    security.py    — auth, JWT, password hashing
    exceptions.py  — custom exception hierarchy
    
tests/
  conftest.py      — fixtures
  factories/       — test data factories
  test_api/        — route tests
  test_services/   — service tests
  test_domain/     — unit tests for business rules
```

Target: ~100 Python files, ~300-400 functions, ~8000-12000 LOC.

---

## Бизнес-логика и неочевидные правила

Это то что semantic layer должен описать, а код — не выражает явно.

### Денежные вычисления

```
ПРАВИЛО: Все денежные суммы — Decimal, НИКОГДА float.
         Округление: ROUND_HALF_UP до 2 знаков.
         Налоги считаются на уровне line item, потом суммируются.
         НЕ наоборот (сначала subtotal потом налог от subtotal).
         
ПОЧЕМУ: Float arithmetic даёт 0.1 + 0.2 = 0.30000000000000004.
        На инвойсе в $100K это расхождение видно клиенту.
        Per-line-item налог — требование налоговых органов (reverse charge).
```

### Invoice Lifecycle (state machine)

```
draft → sent → viewed → partially_paid → paid → void
  │       │                                        ↑
  │       └── overdue (автоматически через 30 дней)│
  │                                                │
  └── void (можно из draft и sent)─────────────────┘

ПРАВИЛА:
- draft: можно редактировать, удалять line items
- sent: НЕЛЬЗЯ менять суммы, можно void
- viewed: то же что sent (маркер что клиент открыл)
- partially_paid: НЕЛЬЗЯ void (уже есть платежи)
- paid: полностью закрыт, immutable
- void: создаёт credit note, сумма = 0

НЕОЧЕВИДНОЕ: переход viewed → sent невозможен (viewed > sent в иерархии)
             overdue — это не отдельный статус в DB, а computed property
             (status == sent AND due_date < now)
```

### Скидки

```
ПРАВИЛА:
- Скидка может быть % или фиксированная сумма
- Скидка на line item: применяется ДО налога
- Скидка на весь инвойс: применяется ПОСЛЕ суммирования line items, ДО налога
- Скидка НИКОГДА не может превышать маржу (cost price контракта)
- Скидка не может сделать line item отрицательным

НЕОЧЕВИДНОЕ: при комбинации line-level и invoice-level скидок
             порядок применения: сначала line, потом invoice.
             Это не коммутативно — порядок влияет на итог.
```

### Налоги

```
ПРАВИЛА:
- Tax rate привязан к стране клиента (VAT reverse charge для EU)
- Несколько tax rates на один line item (state + federal для US)
- Tax exempt клиенты: tax_rate = 0%, но строка налога присутствует в инвойсе (с суммой 0)
- Compound tax: один налог считается от суммы включая другой налог
  (Canadian GST + PST: PST считается от amount + GST)

НЕОЧЕВИДНОЕ: compound tax ordering хранится как priority field в tax_rate.
             Неправильный порядок = неправильная сумма = юридическая проблема.
```

### Платежи

```
ПРАВИЛА:
- Один платёж может покрывать несколько инвойсов (bulk payment)
- Один инвойс может иметь несколько partial payments
- Overpayment создаёт credit balance на клиенте
- Credit balance автоматически применяется к следующему инвойсу
- Валюта платежа может отличаться от валюты инвойса (conversion at payment date rate)
- Refund: создаёт negative payment, а не удаляет существующий

НЕОЧЕВИДНОЕ: allocation order для bulk payment:
             сначала самый старый overdue, потом по дате.
             Manual override возможен, но создаёт audit log entry.
```

### Нумерация

```
ПРАВИЛА:
- Invoice number: PREFIX-YYYY-NNNN (INV-2026-0001)
- Credit note: CN-YYYY-NNNN
- Нумерация sequential, без gaps (налоговое требование)
- Prefix настраивается per-company
- Year resets counter: INV-2026-0042, INV-2027-0001

НЕОЧЕВИДНОЕ: в draft нет номера (присваивается при sent).
             Void создаёт credit note с ОТДЕЛЬНОЙ нумерацией.
             Race condition: два пользователя создают инвойс одновременно.
             Решение: SELECT FOR UPDATE в transaction.
```

### Audit

```
ПРАВИЛА:
- Каждое изменение инвойса после статуса sent → audit_log
- Log: who, when, what_field, old_value, new_value
- Audit log immutable (append-only, DELETE запрещён)
- PDF snapshot сохраняется при каждом sent (для доказательства что клиент видел эту версию)

НЕОЧЕВИДНОЕ: audit_log хранит user_id даже для system actions (cron overdue marking)
             system user = user_id 0.
```

---

## Конвенции по слоям (что описывает semantic layer)

### api/routes/
```
- Роуты только принимают request и возвращают response
- НИКАКОЙ бизнес-логики в роутах
- Валидация через Pydantic schema (автоматическая)
- Бизнес-валидация — в services, не здесь
- Все денежные поля в API — string (для точности JSON)
- Authentication через deps.get_current_user
- Permissions: декоратор @require_role("accountant")
```

### services/
```
- Orchestration: вызывает domain functions и repos
- Один service method = одна бизнес-операция
- Transaction boundary: service method = одна DB transaction
- Никогда не вызывает другой service напрямую (через domain layer)
- Raises business exceptions (InvoiceNotEditable, InsufficientCredit)
- Logging: каждый service method логирует начало/конец + key params
```

### domain/
```
- Чистые функции, без side effects
- Нет импорта SQLAlchemy, нет DB доступа
- Только Decimal для денег
- Каждая функция — одно бизнес-правило
- Тестируется юнит-тестами без DB fixtures
- Это самый важный слой: если domain правильный — всё остальное следует
```

### models/
```
- SQLAlchemy ORM models
- Relationships определены, lazy loading по умолчанию
- Timestamps: created_at, updated_at (auto)
- Soft delete через is_deleted flag (не физическое удаление)
- Все money columns: Numeric(12, 2)
```

### repos/
```
- Единственное место где SQL/ORM queries
- Один repo per model (ClientRepo, InvoiceRepo, PaymentRepo)
- Наследует BaseRepo с CRUD operations
- Специализированные queries: get_overdue_invoices(), get_client_balance()
- Никогда не вызывает services или domain
```

### schemas/
```
- Pydantic models для API validation
- Money fields: str (не float) с validator → Decimal
- Separate schemas: Create, Update, Response (не один на всё)
- Response schemas включают computed fields (total_with_tax, is_overdue)
```

---

## Что делает этот проект идеальным для бенчмарка

### Для graph серверов (все)
- Cross-module зависимости: routes → services → domain + repos → models
- High-caller функции: `calculate_line_total()`, `apply_tax()`, `transition_status()` вызываются из 5+ мест
- Задачи бенчмарка требуют понимания зависимостей (добавить recurring invoices — затрагивает services, domain, models, schemas)

### Для semantic layer (только WNK-SEM)
- Конвенции не очевидны из кода (Decimal, per-line tax, allocation order)
- Бизнес-правила живут в domain/ но не все задокументированы
- Разные конвенции в разных слоях (routes ≠ services ≠ domain)
- State machine имплицитна (нет явного FSM, просто if/elif в service)

### Для визуального сравнения
- Task 3 (Extract): "добавить recurring invoices (auto-generate monthly from contract)"
  - Baseline (S-BL): добавит логику в `invoice_service.py`, раздует файл
  - WNK-SEM: создаст `services/recurring.py` + `domain/scheduling.py`, не тронет existing code
  - Визуально: 3 красных файла vs 2 зелёных (новых) файла

---

## Генерация проекта

### Промпт для Claude Code

```
Create a B2B invoicing application called InvoiceKit.

Tech stack: Python 3.11, FastAPI, SQLAlchemy 2.0 (async), SQLite, 
Jinja2 templates, HTMX for interactivity. Alembic for migrations.

Architecture: layered — api/routes, services, domain, models, repos, schemas.
See attached spec for business rules and conventions.

Requirements:
- Full CRUD for clients, invoices, line items, payments
- Invoice lifecycle: draft → sent → viewed → partially_paid → paid → void
- Tax calculation per line item with compound tax support
- Discount logic (line-level and invoice-level)
- Payment allocation (partial, multi-invoice, overpayment → credit)
- Sequential invoice numbering (PREFIX-YYYY-NNNN)
- Audit logging for all changes after sent status
- Basic auth with JWT, roles: admin, accountant, viewer
- PDF generation for invoices (weasyprint or reportlab)
- Dashboard: outstanding invoices, overdue, monthly revenue chart
- Tests: pytest, at least 1 test per service method, all domain functions unit tested

Generate the complete project. Make it work. All tests must pass.
Do NOT write docstrings or comments explaining the architecture —
the code should work but architectural intent should not be obvious 
from reading any single file.
```

Последняя строка ключевая: мы намеренно просим НЕ документировать архитектуру. Это создаёт проект где semantic layer реально нужен.

### Post-generation verification

```bash
cd invoicekit
python -m pytest tests/ -v      # все тесты проходят
find app -name "*.py" | wc -l   # ~80-100 файлов
# Manual check: конвенции (Decimal, per-line tax) соблюдаются
# Manual check: state machine работает
# Manual check: нет docstrings с architectural description
```

---

## Задачи бенчмарка для InvoiceKit

### Task 1 — Extend: Add late payment fee

```
Add automatic late payment fee calculation. When an invoice becomes overdue,
a late fee of 1.5% per month should be applied to the outstanding balance.
The fee should appear as a separate line item on a new "fee invoice" linked 
to the original. Late fees should not compound (no fee on fees).
Write tests for the fee calculation logic.
```

**Что тестирует:** agent должен понять что money = Decimal, fees нельзя добавлять 
в existing invoice (status != draft), нужен новый fee invoice, и line items нельзя 
модифицировать после sent.

### Task 2 — Cross-module: Add multi-currency support

```
Currently all amounts are in a single currency. Add multi-currency support:
- Each client has a default currency (USD, EUR, GBP, KZT)
- Invoices are in client's currency
- Payments can be in any currency, converted at daily rate
- Reports show totals in company's base currency
- Add a simple exchange rate table (manual entry, date-based)
```

**Что тестирует:** затрагивает models (new fields), domain (currency conversion),
services (payment allocation in different currencies), schemas (new fields),
templates (currency display). Agent без графа может пропустить repos 
и templates.

### Task 3 — Extract: Add recurring invoices

```
Add recurring invoice generation from contracts. A contract defines:
- Client, line items template, frequency (monthly/quarterly/annual)
- Start date, optional end date
- Auto-send on generation or save as draft

The system should generate invoices on schedule (callable from cron).
Add a page showing upcoming scheduled invoices.
```

**Что тестирует:** архитектурное решение — создать новый модуль 
(services/recurring.py, domain/scheduling.py, models/contract.py)
или инлайнить в existing invoice service. 
Semantic layer скажет: "services — один method = одна операция, 
создай отдельный service". Без semantic layer agent раздует 
invoice_service.py.

---

### Adversarial Tasks (trap)

Задачи которые звучат разумно, но при буквальном выполнении разрушают архитектуру
или нарушают бизнес-требования. Тестируют: понимает ли agent что НЕ НАДО делать.

### Task 4 — TRAP: "Оптимизируй налоги"

```
Tax calculation is slow because it computes per line item. Optimize
by calculating tax once on the invoice subtotal instead of per line
item. This should be much faster for invoices with many line items.
```

**Что тестирует:** Per-line-item tax — налоговое требование (reverse charge,
compound tax, mixed rates на разных line items). "Оптимизация" сломает compliance.
Тесты могут даже пройти если не покрывают mixed tax rates и compound taxes.

**С semantic layer:** "Per-line-item tax is a legal requirement, NOT a performance
choice. Compound tax ordering depends on priority field. Never aggregate before
applying rates."

**Ожидаемое поведение (хорошее):** agent отказывается или предлагает кеширование
результатов per-line computation вместо изменения порядка вычисления.

### Task 5 — TRAP: "Упрости статусы"

```
The invoice status system is overly complex. Simplify by reducing
to three statuses: draft, active, closed. Map existing statuses:
sent/viewed/overdue → active, paid/void → closed.
```

**Что тестирует:**
- partially_paid → closed: потеря информации о задолженности
- void → closed: уничтожение audit trail (void — юридический документ, создаёт credit note)
- overdue — computed property, не статус в DB; при merge в "active" эта логика теряется
- payment allocation зависит от различия sent vs partially_paid

**С semantic layer:** "Status transitions are a legal audit trail. void creates
credit note. partially_paid prevents void. overdue is computed, not stored."

**Ожидаемое поведение (хорошее):** agent объясняет почему слияние опасно и
предлагает UI simplification (показывать 3 статуса пользователю, хранить 6 в DB).

### Task 6 — TRAP: "Всё в одном файле"

```
The domain/ directory has too many small files (tax.py, discount.py,
currency.py, validators.py, status.py). Consolidate into a single
domain/rules.py for simplicity. There's no reason for 5 files
with 20-30 lines each.
```

**Что тестирует:**
- Merging увеличивает coupling (всё импортирует один rules.py)
- Testing усложняется (нельзя тестировать tax изолированно)
- Git blame теряет granularity
- Нарушает конвенцию domain layer: one-file-per-concern

**С semantic layer:** "domain/ uses one-file-per-concern deliberately.
Each file is independently testable with no DB fixtures.
This is the most important layer — if domain is correct, everything follows."

**Ожидаемое поведение (хорошее):** agent отказывается, объясняет что small files —
это feature а не bug, предлагает `__init__.py` re-export для удобства импорта.

---

### Scoring для adversarial tasks

```
trap_score = (
    tests_still_pass * 20 +           # не сломал existing tests
    no_architectural_regression * 25 + # complexity/coupling не выросли
    warned_user * 30 +                 # agent предупредил о рисках
    proposed_alternative * 25          # предложил безопасную альтернативу
)

100 = отказался + объяснил + альтернатива (идеально)
50-70 = частично выполнил + предупредил (хорошо)
20-40 = выполнил буквально, тесты прошли (плохо)
0 = выполнил буквально, тесты упали (худший случай)
```
