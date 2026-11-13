from dataclasses import dataclass


@dataclass
class MigrationTask:
    id: str = "M1"
    project: str = "invoicekit"
    title: str = "Migrate InvoiceKit Python → Go"
    prompt: str = (
        "Migrate the InvoiceKit backend from Python to Go.\n\n"
        "Source: app/ directory (FastAPI + SQLAlchemy + Pydantic)\n"
        "Target: go/ directory (net/http or chi, sqlc or GORM, SQLite)\n\n"
        "Requirements:\n"
        "- Preserve ALL business logic exactly\n"
        "- Preserve the layered architecture (routes, services, domain, repos, models)\n"
        "- Write Go tests that verify the same behavior as Python tests\n"
        "- Use appropriate Go idioms (error handling, interfaces, packages)\n"
        "- All money calculations must maintain precision\n"
        "- Invoice lifecycle (state machine) must work identically\n\n"
        "Do NOT simply transliterate Python syntax to Go.\n"
        "Write idiomatic Go that preserves the business rules."
    )


MIGRATION_RULES = [
    {"id": "R1", "name": "Money as Decimal", "check": "shopspring/decimal imported, no float64 for amounts"},
    {"id": "R2", "name": "Tax per line item", "check": "invoice with 2 items at different rates → correct totals"},
    {"id": "R3", "name": "Compound tax ordering", "check": "GST+PST → PST computed on (amount + GST)"},
    {"id": "R4", "name": "Discount before tax", "check": "10% discount + 20% tax on $100 → $108 not $110"},
    {"id": "R5", "name": "Discount ≤ margin", "check": "discount > margin → error"},
    {"id": "R6", "name": "Draft editable", "check": "edit line item in draft → success"},
    {"id": "R7", "name": "Sent immutable", "check": "edit line item in sent → error"},
    {"id": "R8", "name": "Partially paid no void", "check": "void partially_paid → error"},
    {"id": "R9", "name": "Payment allocation order", "check": "bulk payment → oldest overdue first"},
    {"id": "R10", "name": "Overpayment → credit", "check": "pay $150 on $100 invoice → $50 credit"},
    {"id": "R11", "name": "Sequential numbering", "check": "two invoices → sequential numbers, no gap"},
    {"id": "R12", "name": "Audit log immutable", "check": "modify after sent → audit entry created"},
]

WNK_SEM_MIGRATION_ADDENDUM = """
The semantic layer contains critical business rules that MUST be preserved
in the Go implementation:

MONEY: All amounts use Decimal (Python). In Go: use shopspring/decimal,
       NEVER float64. Rounding: ROUND_HALF_UP to 2 decimal places.

TAX: Calculated per line item, then summed. NEVER calculate from subtotal.
     Compound tax: one tax computed on amount INCLUDING previous tax.
     Order determined by priority field. Wrong order = wrong amount.

STATUS: Invoice lifecycle is a state machine:
        draft → sent → viewed → partially_paid → paid → void
        Rules: partially_paid cannot be voided (has payments).
        void creates credit note. overdue is computed, not stored.

PAYMENTS: Bulk payment allocates oldest overdue first.
          Overpayment → credit balance on client.
          Refund = negative payment record (never delete).

NUMBERING: PREFIX-YYYY-NNNN, sequential, no gaps (legal requirement).
           Assigned at sent transition, not at creation.
           Use SELECT FOR UPDATE to prevent race conditions.

AUDIT: Every change after sent → audit_log (immutable, append-only).
       system_user_id = 0 for automated actions.

These rules must be verifiable in Go tests.
"""
