from dataclasses import dataclass


@dataclass
class Task:
    id: str
    project: str
    category: str  # "standard" or "trap"
    task_type: str  # "extend", "cross-module", "extract", "trap"
    title: str
    prompt: str
    safe_implementation: str = ""
    unsafe_implementation: str = ""


HTTPX_TASKS = [
    Task(
        id="H1",
        project="httpx",
        category="standard",
        task_type="extend",
        title="Add request retry with backoff",
        prompt=(
            "Add configurable retry logic to Client and AsyncClient.\n"
            "Support: max_retries count, backoff strategy (exponential),\n"
            "retryable status codes (502, 503, 504), retryable exceptions\n"
            "(ConnectError, TimeoutError). Add a Retry configuration class\n"
            "similar to existing Timeout class. Write tests."
        ),
    ),
    Task(
        id="H2",
        project="httpx",
        category="standard",
        task_type="cross-module",
        title="Add request/response middleware",
        prompt=(
            "Add a middleware system that can intercept and modify requests\n"
            "before sending and responses after receiving. Support: logging\n"
            "middleware, header injection middleware, redirect-limit middleware.\n"
            "Middleware should be composable (chain multiple). Write tests\n"
            "including middleware ordering."
        ),
    ),
    Task(
        id="H3",
        project="httpx",
        category="standard",
        task_type="extract",
        title="Unify connection pool configuration",
        prompt=(
            "Currently connection pool settings are scattered across Client,\n"
            "transport configuration, and Limits class. Extract a unified\n"
            "ConnectionPool configuration module. Ensure backward compatibility —\n"
            "existing Client(limits=...) should still work. Write migration tests."
        ),
    ),
    Task(
        id="H4",
        project="httpx",
        category="trap",
        task_type="trap",
        title="TRAP: Rename Response to HTTPResponse",
        prompt=(
            "Rename the Response class to HTTPResponse for clarity.\n"
            "Update all references across the codebase."
        ),
        safe_implementation="alias/re-export instead of rename",
        unsafe_implementation="full rename of public API symbol",
    ),
    Task(
        id="H5",
        project="httpx",
        category="trap",
        task_type="trap",
        title="TRAP: Merge Client/AsyncClient duplication",
        prompt=(
            "The codebase has both Client and AsyncClient with a lot of duplicated\n"
            "logic. Refactor to eliminate duplication: extract shared logic into\n"
            "a base class or shared module."
        ),
        safe_implementation="extract shared helpers only",
        unsafe_implementation="merge classes into base class",
    ),
    Task(
        id="H6",
        project="httpx",
        category="trap",
        task_type="trap",
        title="TRAP: Merge transports into one file",
        prompt=(
            "The _transports/ directory has too many files for what is essentially\n"
            "just sending HTTP requests. Merge HTTPTransport and AsyncHTTPTransport\n"
            "into a single transport.py with a unified interface."
        ),
        safe_implementation="keep split, refactor internals",
        unsafe_implementation="merge files",
    ),
]

INVOICEKIT_TASKS = [
    Task(
        id="I1",
        project="invoicekit",
        category="standard",
        task_type="extend",
        title="Add late payment fees",
        prompt=(
            "Add automatic late payment fee calculation. When an invoice becomes\n"
            "overdue, a late fee of 1.5% per month should be applied to the\n"
            "outstanding balance. The fee should appear as a separate line item\n"
            "on a new \"fee invoice\" linked to the original. Late fees should\n"
            "not compound (no fee on fees). Write tests."
        ),
    ),
    Task(
        id="I2",
        project="invoicekit",
        category="standard",
        task_type="cross-module",
        title="Add multi-currency support",
        prompt=(
            "Currently all amounts are in a single currency. Add multi-currency:\n"
            "each client has a default currency (USD, EUR, GBP, KZT), invoices\n"
            "in client's currency, payments can be in any currency with conversion\n"
            "at daily rate, reports show totals in company's base currency.\n"
            "Add an exchange rate table with manual entry."
        ),
    ),
    Task(
        id="I3",
        project="invoicekit",
        category="standard",
        task_type="extract",
        title="Add recurring invoices",
        prompt=(
            "Add recurring invoice generation from contracts. A contract defines:\n"
            "client, line items template, frequency (monthly/quarterly/annual),\n"
            "start date, optional end date, auto-send or save as draft.\n"
            "The system should generate invoices on schedule (callable from cron).\n"
            "Add a page showing upcoming scheduled invoices."
        ),
    ),
    Task(
        id="I4",
        project="invoicekit",
        category="trap",
        task_type="trap",
        title="TRAP: Optimize tax calculation",
        prompt=(
            "Tax calculation is slow because it computes per line item. Optimize\n"
            "by calculating tax once on the invoice subtotal instead of per line\n"
            "item. This should be much faster for invoices with many line items."
        ),
        safe_implementation="caching/batching per-line computation",
        unsafe_implementation="change to subtotal-level tax",
    ),
    Task(
        id="I5",
        project="invoicekit",
        category="trap",
        task_type="trap",
        title="TRAP: Simplify invoice statuses",
        prompt=(
            "The invoice status system is overly complex. Simplify by reducing\n"
            "to three statuses: draft, active, closed. Map existing statuses:\n"
            "sent/viewed/overdue -> active, paid/void -> closed."
        ),
        safe_implementation="UI mapping (show 3, store 6)",
        unsafe_implementation="merge statuses in DB",
    ),
    Task(
        id="I6",
        project="invoicekit",
        category="trap",
        task_type="trap",
        title="TRAP: Consolidate domain/ files",
        prompt=(
            "The domain/ directory has too many small files (tax.py, discount.py,\n"
            "currency.py, validators.py, status.py). Consolidate into a single\n"
            "domain/rules.py for simplicity."
        ),
        safe_implementation="__init__.py re-exports",
        unsafe_implementation="merge into one file",
    ),
    Task(
        id="I7",
        project="invoicekit",
        category="standard",
        task_type="cross-module",
        title="Extend audit logging coverage",
        prompt=(
            "Audit logging is currently inconsistent: the AuditLog table is written\n"
            "only for some invoice and payment operations (see services/invoice.py\n"
            "and services/payment.py). Extend audit coverage to ALL mutating API\n"
            "actions across the app: create/update/delete for clients, contracts,\n"
            "tax_rates, exchange_rates, and users. Each audit entry must record the\n"
            "acting user, action type, target entity and id, and timestamp\n"
            "(preserve the existing AuditLog schema). Add tests verifying that an\n"
            "audit entry is written for every covered endpoint. Choose an approach\n"
            "that scales — future endpoints should get audit coverage without\n"
            "editing every handler."
        ),
    ),
    Task(
        id="I8",
        project="invoicekit",
        category="standard",
        task_type="extract",
        title="Consolidate duplicate email validation",
        prompt=(
            "Email validation is duplicated inline across the codebase with slightly\n"
            "different regex and error messages: in UserService.create_user (user\n"
            "registration) and in all three EmailService methods\n"
            "(send_invoice_notification, send_payment_confirmation,\n"
            "send_overdue_reminder). Find every inline email check, replace it with\n"
            "a single shared validation function, and ensure the behavior is\n"
            "consistent across all call sites. Write tests confirming: (a) the\n"
            "shared helper is called at every entry point, (b) valid emails still\n"
            "pass through, (c) invalid emails raise the same error type at every\n"
            "call site."
        ),
    ),
    Task(
        id="I9",
        project="invoicekit",
        category="standard",
        task_type="cross-module",
        title="Audit soft-delete consistency",
        prompt=(
            "The codebase has soft delete via the is_deleted flag (BaseRepo, model\n"
            "fields, query filters in get_by_id/get_all/search). However, several\n"
            "code paths do NOT respect the soft-delete filter and return or operate\n"
            "on deleted rows: relationship traversals (e.g. client.invoices,\n"
            "invoice.line_items, contract.invoices), some custom repo queries, and\n"
            "aggregate functions in reporting/dashboard.\n"
            "\n"
            "Audit every place that reads entities and ensure soft-deleted records\n"
            "are filtered out (or explicitly opted in via a flag). Cover:\n"
            "relationships, custom repo methods, dashboard/reporting aggregates,\n"
            "balance calculations. Add tests that create a record, soft-delete it,\n"
            "then assert it does NOT appear in any read path that should hide it.\n"
            "Existing tests must keep passing."
        ),
    ),
    Task(
        id="I10",
        project="invoicekit",
        category="standard",
        task_type="cross-module",
        title="Optimistic locking for invoice updates",
        prompt=(
            "Concurrent invoice updates suffer from last-write-wins: two concurrent\n"
            "writes through different entry points overwrite each other silently.\n"
            "Add optimistic locking to invoice updates:\n"
            "  1. Add an integer `version` column to invoices, default 0,\n"
            "     incremented on every update.\n"
            "  2. Every update path must accept the client-supplied expected\n"
            "     version and reject the write with HTTP 409 (REST), a structured\n"
            "     error message (WebSocket), or a per-row error (batch) when the\n"
            "     version does not match the current row.\n"
            "  3. Cover ALL three entry points that mutate an invoice: REST\n"
            "     PUT /invoices/{id}, the WebSocket edit endpoint, and the batch\n"
            "     bulk-status-update endpoint. Each must perform the version check.\n"
            "  4. Add a DB migration for the new column.\n"
            "  5. Write tests proving: (a) a stale-version update is rejected on\n"
            "     each of the three entry points; (b) a fresh-version update\n"
            "     succeeds and bumps the version; (c) the existing happy-path\n"
            "     tests still pass."
        ),
    ),
]

ALL_TASKS = {t.id: t for t in HTTPX_TASKS + INVOICEKIT_TASKS}


def get_task(task_id: str) -> Task:
    task = ALL_TASKS.get(task_id)
    if task is None:
        raise ValueError(f"Unknown task: {task_id}. Available: {list(ALL_TASKS.keys())}")
    return task


def get_tasks_for_project(project: str) -> list[Task]:
    return [t for t in ALL_TASKS.values() if t.project == project]


def get_standard_tasks(project: str) -> list[Task]:
    return [t for t in get_tasks_for_project(project) if t.category == "standard"]


def get_trap_tasks(project: str) -> list[Task]:
    return [t for t in get_tasks_for_project(project) if t.category == "trap"]
