"""Double-entry accounting engine — validation, numbering, posting, reversal.

Every financial mutation in the platform funnels through this module so that the
rules (balance, open period, posting immutability, idempotency, audit trail)
are enforced in one auditable place.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from db import to_cents, to_milli
from security import audit, notify, utcnow

# Document number prefixes
DOC_PREFIX = {
    "journal_entry": "JE",
    "sales_invoice": "SI",
    "purchase_invoice": "PI",
    "receipt": "RV",
    "payment": "PV",
    "expense": "EX",
    "depreciation": "DEP",
    "payroll": "PAY",
    "transfer": "TRF",
    "asset": "FA",
    "debt_item": "DEB",
    "debt_payment": "DPV",
}

# Amount threshold (in piastres) above which Financial Manager approval is mandatory
APPROVAL_THRESHOLD_CENTS = 20_000_000  # 200,000 EGP


class ValidationError(Exception):
    def __init__(self, message, details=None, status=422):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.status = status


# --------------------------------------------------------------------------
# Numbering and fiscal calendar
# --------------------------------------------------------------------------
def next_number(conn, company_id: int, doc_type: str, on_date: str) -> str:
    year = int(str(on_date)[:4])
    conn.execute(
        "INSERT INTO document_sequences (company_id, doc_type, year, last_number) VALUES (?,?,?,0) "
        "ON CONFLICT(company_id, doc_type, year) DO NOTHING",
        (company_id, doc_type, year),
    )
    conn.execute(
        "UPDATE document_sequences SET last_number = last_number + 1 "
        "WHERE company_id = ? AND doc_type = ? AND year = ?",
        (company_id, doc_type, year),
    )
    row = conn.execute(
        "SELECT last_number FROM document_sequences WHERE company_id=? AND doc_type=? AND year=?",
        (company_id, doc_type, year),
    ).fetchone()
    prefix = DOC_PREFIX.get(doc_type, "DOC")
    return f"{prefix}-{year}-{row['last_number']:05d}"


def ensure_period_open(conn, company_id: int, on_date: str) -> None:
    row = conn.execute(
        "SELECT status, period_no FROM fiscal_periods WHERE company_id=? AND start_date<=? AND end_date>=?",
        (company_id, on_date, on_date),
    ).fetchone()
    if row is None:
        raise ValidationError(
            "PERIOD_NOT_DEFINED: لا توجد فترة مالية معرّفة لهذا التاريخ / No fiscal period defined for this date",
            {"date": on_date},
        )
    if row["status"] != "open":
        raise ValidationError(
            "PERIOD_CLOSED: الفترة المالية مقفلة ولا يمكن الترحيل إليها / Fiscal period is closed",
            {"date": on_date, "period_no": row["period_no"]},
        )


# --------------------------------------------------------------------------
# Journal entry lifecycle
# --------------------------------------------------------------------------
def create_entry(conn, user, *, company_id, entry_date, memo, lines, entry_type="manual",
                 branch_id=None, currency="EGP", fx_rate=1.0, cost_center_id=None,
                 project_id=None, source_type=None, source_id=None, idempotency_key=None,
                 auto_post=False) -> int:
    """Create a journal entry from a list of {account_id, debit, credit, ...} lines.

    `lines` accept amounts already in cents via debit_cents/credit_cents.
    """
    if idempotency_key:
        existing = conn.execute(
            "SELECT id FROM journal_entries WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            return existing["id"]

    clean = []
    total_debit = total_credit = 0
    for idx, ln in enumerate(lines, start=1):
        debit = int(ln.get("debit_cents", 0) or 0)
        credit = int(ln.get("credit_cents", 0) or 0)
        if debit < 0 or credit < 0:
            raise ValidationError("NEGATIVE_AMOUNT: المبالغ يجب أن تكون موجبة / Amounts must be positive")
        if debit and credit:
            raise ValidationError("LINE_BOTH_SIDES: لا يمكن أن يحتوي السطر على مدين ودائن معًا / A line cannot be both debit and credit")
        if not debit and not credit:
            continue
        acc = conn.execute(
            "SELECT id, is_postable, is_active, company_id FROM accounts WHERE id = ?", (ln["account_id"],)
        ).fetchone()
        if acc is None or acc["company_id"] != company_id:
            raise ValidationError("INVALID_ACCOUNT: حساب غير صالح / Invalid account")
        if not acc["is_postable"]:
            raise ValidationError(
                "NON_POSTABLE_ACCOUNT: لا يمكن الترحيل إلى حساب تجميعي / Cannot post to a parent (summary) account",
                {"account_id": ln["account_id"]},
            )
        if not acc["is_active"]:
            raise ValidationError("INACTIVE_ACCOUNT: الحساب غير نشط / Account is inactive")
        clean.append({
            "line_no": idx,
            "account_id": ln["account_id"],
            "debit_cents": debit,
            "credit_cents": credit,
            "description": ln.get("description") or memo,
            "cost_center_id": ln.get("cost_center_id", cost_center_id),
            "project_id": ln.get("project_id", project_id),
            "customer_id": ln.get("customer_id"),
            "vendor_id": ln.get("vendor_id"),
        })
        total_debit += debit
        total_credit += credit

    if not clean:
        raise ValidationError("EMPTY_ENTRY: القيد لا يحتوي على سطور / Entry has no lines")
    if total_debit != total_credit:
        raise ValidationError(
            "UNBALANCED: إجمالي المدين لا يساوي إجمالي الدائن / Debit total must equal credit total",
            {"debit": total_debit / 100, "credit": total_credit / 100, "difference": (total_debit - total_credit) / 100},
        )
    ensure_period_open(conn, company_id, entry_date)

    entry_no = next_number(conn, company_id, "journal_entry", entry_date)
    cur = conn.execute(
        """INSERT INTO journal_entries
           (company_id, branch_id, entry_no, entry_date, entry_type, memo, status, currency, fx_rate,
            total_debit_cents, total_credit_cents, cost_center_id, project_id, source_type, source_id,
            idempotency_key, created_by, created_at)
           VALUES (?,?,?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, branch_id, entry_no, entry_date, entry_type, memo, currency, fx_rate,
         total_debit, total_credit, cost_center_id, project_id, source_type, source_id,
         idempotency_key, user["id"] if user else None, utcnow()),
    )
    entry_id = cur.lastrowid
    for ln in clean:
        conn.execute(
            """INSERT INTO journal_lines (entry_id, line_no, account_id, debit_cents, credit_cents,
               description, cost_center_id, project_id, customer_id, vendor_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (entry_id, ln["line_no"], ln["account_id"], ln["debit_cents"], ln["credit_cents"],
             ln["description"], ln["cost_center_id"], ln["project_id"], ln["customer_id"], ln["vendor_id"]),
        )
    audit(conn, user, "journal.create", "journal_entry", entry_id,
          {"entry_no": entry_no, "total": total_debit / 100}, company_id=company_id)
    return entry_id


def _get_entry(conn, entry_id: int, company_id: int = None):
    row = conn.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise ValidationError("NOT_FOUND: القيد غير موجود / Entry not found")
    if company_id is not None and row["company_id"] != company_id:
        raise ValidationError("NOT_FOUND: القيد غير موجود / Entry not found")
    return row


def submit_entry(conn, user, entry_id: int, company_id: int, comment: str = ""):
    e = _get_entry(conn, entry_id, company_id)
    if e["status"] != "draft":
        raise ValidationError("INVALID_STATE: لا يمكن إرسال قيد بحالة " + e["status"])
    if e["created_by"] == user["id"]:
        raise ValidationError("SOD_VIOLATION: لا يمكن للمنشئ مراجعة قيده (فصل المهام) / Segregation of duties: creator cannot review own entry")
    conn.execute("UPDATE journal_entries SET status='review', submitted_by=?, submitted_at=? WHERE id=?",
                 (user["id"], utcnow(), entry_id))
    audit(conn, user, "journal.submit", "journal_entry", entry_id, {"comment": comment}, company_id=company_id)
    notify(conn, company_id, None, "approval",
           f"قيد بانتظار المراجعة {e['entry_no']}", f"Entry pending review {e['entry_no']}",
           comment or "", comment or "", f"#/journal/{entry_id}")


def review_entry(conn, user, entry_id: int, company_id: int, decision: str = "approve", comment: str = ""):
    e = _get_entry(conn, entry_id, company_id)
    if e["status"] != "review":
        raise ValidationError("INVALID_STATE: القيد ليس بانتظار المراجعة")
    if e["created_by"] == user["id"]:
        raise ValidationError("SOD_VIOLATION: لا يمكن للمنشئ اعتماد قيده / Segregation of duties violated")
    if decision == "reject":
        conn.execute("UPDATE journal_entries SET status='draft' WHERE id=?", (entry_id,))
        audit(conn, user, "journal.reject", "journal_entry", entry_id, {"comment": comment}, company_id=company_id)
        notify(conn, company_id, e["created_by"], "rejection",
               f"تم رفض القيد {e['entry_no']}", f"Entry rejected {e['entry_no']}", comment, comment,
               f"#/journal/{entry_id}")
        return {"status": "draft"}
    # Amount-based escalation to Financial Manager
    if e["total_debit_cents"] >= APPROVAL_THRESHOLD_CENTS:
        conn.execute("UPDATE journal_entries SET status='approved' WHERE id=?", (entry_id,))
        audit(conn, user, "journal.review", "journal_entry", entry_id,
              {"decision": "approved_pending_fm", "comment": comment}, company_id=company_id)
        return {"status": "approved", "note": "REQUIRES_APPROVAL"}
    conn.execute(
        "UPDATE journal_entries SET status='approved', approved_by=?, approved_at=? WHERE id=?",
        (user["id"], utcnow(), entry_id),
    )
    audit(conn, user, "journal.review", "journal_entry", entry_id, {"decision": "approved", "comment": comment},
          company_id=company_id)
    return {"status": "approved"}


def approve_entry(conn, user, entry_id: int, company_id: int, comment: str = ""):
    e = _get_entry(conn, entry_id, company_id)
    if e["status"] not in ("review", "approved"):
        raise ValidationError("INVALID_STATE: القيد غير قابل للاعتماد في حالته الحالية")
    if e["total_debit_cents"] < APPROVAL_THRESHOLD_CENTS and e["status"] == "review":
        raise ValidationError("THRESHOLD: هذا القيد لا يتطلب اعتماد المدير المالي / Below approval threshold")
    if e["created_by"] == user["id"] or e["approved_by"] == user["id"]:
        raise ValidationError("SOD_VIOLATION: فصل المهام مطلوب / Segregation of duties required")
    conn.execute("UPDATE journal_entries SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                 (user["id"], utcnow(), entry_id))
    audit(conn, user, "journal.approve", "journal_entry", entry_id, {"comment": comment}, company_id=company_id)
    notify(conn, company_id, e["created_by"], "approval",
           f"تم اعتماد القيد {e['entry_no']}", f"Entry approved {e['entry_no']}", comment, comment,
           f"#/journal/{entry_id}")
    return {"status": "approved"}


def post_entry(conn, user, entry_id: int, company_id: int):
    e = _get_entry(conn, entry_id, company_id)
    if e["status"] == "posted":
        raise ValidationError("ALREADY_POSTED: القيد مرحّل بالفعل / Entry already posted")
    if e["status"] != "approved":
        raise ValidationError("NOT_APPROVED: يجب اعتماد القيد قبل الترحيل / Entry must be approved before posting")
    ensure_period_open(conn, company_id, e["entry_date"])
    lines = conn.execute(
        "SELECT COALESCE(SUM(debit_cents),0) d, COALESCE(SUM(credit_cents),0) c FROM journal_lines WHERE entry_id=?",
        (entry_id,),
    ).fetchone()
    if lines["d"] != lines["c"] or lines["d"] == 0:
        raise ValidationError("UNBALANCED: القيد غير متوازن / Entry is not balanced")
    conn.execute(
        "UPDATE journal_entries SET status='posted', posted_by=?, posted_at=? WHERE id=? AND status='approved'",
        (user["id"], utcnow(), entry_id),
    )
    audit(conn, user, "journal.post", "journal_entry", entry_id, {"entry_no": e["entry_no"]}, company_id=company_id)
    return {"status": "posted", "entry_no": e["entry_no"]}


def reverse_entry(conn, user, entry_id: int, company_id: int, on_date: str = None, reason: str = ""):
    e = _get_entry(conn, entry_id, company_id)
    if e["status"] != "posted":
        raise ValidationError("INVALID_STATE: يمكن عكس القيود المرحلة فقط / Only posted entries can be reversed")
    if e["reversed_by_id"]:
        raise ValidationError("ALREADY_REVERSED: تم عكس هذا القيد مسبقًا / Entry already reversed")
    on_date = on_date or date.today().isoformat()
    ensure_period_open(conn, company_id, on_date)
    lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,)).fetchall()
    mirror = [{
        "account_id": l["account_id"],
        "debit_cents": l["credit_cents"],
        "credit_cents": l["debit_cents"],
        "description": f"عكس القيد {e['entry_no']} — {reason}".strip(),
        "cost_center_id": l["cost_center_id"], "project_id": l["project_id"],
        "customer_id": l["customer_id"], "vendor_id": l["vendor_id"],
    } for l in lines]
    new_id = create_entry(
        conn, user, company_id=company_id, entry_date=on_date, entry_type="reversal",
        memo=f"عكس القيد {e['entry_no']} — {reason}".strip(), lines=mirror,
        branch_id=e["branch_id"], source_type="reversal", source_id=entry_id,
        idempotency_key=f"reversal:{entry_id}",
    )
    conn.execute("UPDATE journal_entries SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                 (user["id"], utcnow(), new_id))
    post_entry(conn, user, new_id, company_id)
    conn.execute("UPDATE journal_entries SET status='reversed', reversed_by_id=? WHERE id=?", (new_id, entry_id))
    audit(conn, user, "journal.reverse", "journal_entry", entry_id,
          {"reversal_entry_id": new_id, "reason": reason}, company_id=company_id)
    return {"reversal_entry_id": new_id}


def auto_post_entry(conn, user, **kwargs) -> int:
    """Create, self-approve under system policy and post — used by operational documents.

    Operational documents (invoices, payments) follow their own approval chain,
    so the generated accounting entry is posted atomically with the document.
    """
    entry_id = create_entry(conn, user, **kwargs)
    conn.execute(
        "UPDATE journal_entries SET status='approved', approved_by=?, approved_at=? WHERE id=?",
        (user["id"] if user else None, utcnow(), entry_id),
    )
    post_entry(conn, user, entry_id, kwargs["company_id"])
    return entry_id


# --------------------------------------------------------------------------
# Account helpers
# --------------------------------------------------------------------------
def account_id_by_code(conn, company_id: int, code: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE company_id=? AND code=?", (company_id, code)).fetchone()
    if row is None:
        raise ValidationError(f"MISSING_ACCOUNT: الحساب {code} غير موجود / Account {code} not found")
    return row["id"]


# --------------------------------------------------------------------------
# Inventory valuation (weighted average)
# --------------------------------------------------------------------------
def inventory_state(conn, company_id: int, product_id: int, warehouse_id: int):
    row = conn.execute(
        "SELECT COALESCE(SUM(qty_milli),0) qty, COALESCE(SUM(value_cents),0) val "
        "FROM stock_movements WHERE company_id=? AND product_id=? AND warehouse_id=?",
        (company_id, product_id, warehouse_id),
    ).fetchone()
    return row["qty"], row["val"]


def stock_in(conn, user, company_id, product_id, warehouse_id, qty_milli, unit_cost_cents,
             move_date, move_type="in", ref_type=None, ref_id=None, journal_entry_id=None, notes=None):
    if qty_milli <= 0:
        raise ValidationError("INVALID_QTY: الكمية يجب أن تكون موجبة / Quantity must be positive")
    qty, val = inventory_state(conn, company_id, product_id, warehouse_id)
    value = int(round(qty_milli * unit_cost_cents / 1000))
    conn.execute(
        """INSERT INTO stock_movements (company_id, product_id, warehouse_id, move_date, move_type,
           qty_milli, unit_cost_cents, value_cents, running_qty_milli, running_value_cents,
           ref_type, ref_id, journal_entry_id, notes, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, product_id, warehouse_id, move_date, move_type, qty_milli, unit_cost_cents, value,
         qty + qty_milli, val + value, ref_type, ref_id, journal_entry_id, notes,
         user["id"] if user else None, utcnow()),
    )
    return value


def stock_out(conn, user, company_id, product_id, warehouse_id, qty_milli, move_date,
              ref_type=None, ref_id=None, journal_entry_id=None, allow_negative=False, notes=None):
    if qty_milli <= 0:
        raise ValidationError("INVALID_QTY: الكمية يجب أن تكون موجبة / Quantity must be positive")
    qty, val = inventory_state(conn, company_id, product_id, warehouse_id)
    if qty_milli > qty and not allow_negative:
        raise ValidationError(
            "INSUFFICIENT_STOCK: الكمية المتاحة غير كافية / Insufficient stock",
            {"available": qty / 1000, "requested": qty_milli / 1000},
        )
    avg = int(round(val * 1000 / qty)) if qty > 0 else 0
    value = int(round(qty_milli * avg / 1000))
    conn.execute(
        """INSERT INTO stock_movements (company_id, product_id, warehouse_id, move_date, move_type,
           qty_milli, unit_cost_cents, value_cents, running_qty_milli, running_value_cents,
           ref_type, ref_id, journal_entry_id, notes, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, product_id, warehouse_id, move_date, "out", -qty_milli, avg, -value,
         qty - qty_milli, val - value, ref_type, ref_id, journal_entry_id, notes,
         user["id"] if user else None, utcnow()),
    )
    return value, avg


def customer_ar_account(conn, company_id, customer_id) -> int:
    row = conn.execute("SELECT account_id FROM customers WHERE id=? AND company_id=?",
                       (customer_id, company_id)).fetchone()
    if row is None:
        raise ValidationError("NOT_FOUND: العميل غير موجود / Customer not found")
    return row["account_id"] or account_id_by_code(conn, company_id, "1210")


def vendor_ap_account(conn, company_id, vendor_id) -> int:
    row = conn.execute("SELECT account_id FROM vendors WHERE id=? AND company_id=?",
                       (vendor_id, company_id)).fetchone()
    if row is None:
        raise ValidationError("NOT_FOUND: المورد غير موجود / Vendor not found")
    return row["account_id"] or account_id_by_code(conn, company_id, "2110")
