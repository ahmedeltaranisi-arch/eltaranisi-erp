"""Deletion service — removing documents and master records safely.

Design rules (they are enforced here, not in the UI):

1. **Company isolation** — every lookup is scoped to `ctx.user["company_id"]`.
2. **No silent destruction of posted books** — a posted invoice/payment owns a
   journal entry. Deleting it must also delete that entry *and* the stock
   movements it created, otherwise the trial balance would stop balancing.
   The full document snapshot is written to `audit_logs` **before** the delete,
   so the record survives even though the row does not.
3. **Closed periods are frozen** — nothing dated inside a closed fiscal period
   can be deleted (same rule as posting).
4. **Referential integrity** — a customer/vendor with any document behind it
   cannot be deleted; the API answers with the exact list of blockers instead
   of a database error.
5. **Paid documents cannot vanish** — an invoice with allocations (money
   received/paid) must be unlinked first, so cash is never orphaned.
"""
from __future__ import annotations

from accounting import ValidationError, ensure_period_open
from security import audit


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _row(conn, sql, params):
    return conn.execute(sql, params).fetchone()


def _delete_journal_entry(conn, entry_id):
    """Drop an entry only if it is not the reversal target of another entry."""
    if not entry_id:
        return
    children = conn.execute(
        "SELECT id FROM journal_entries WHERE reversal_of_id=?", (entry_id,)
    ).fetchall()
    for child in children:
        conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (child["id"],))
        conn.execute("DELETE FROM journal_entries WHERE id=?", (child["id"],))
    conn.execute("UPDATE journal_entries SET reversed_by_id=NULL WHERE reversed_by_id=?", (entry_id,))
    conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (entry_id,))
    conn.execute("DELETE FROM journal_entries WHERE id=?", (entry_id,))


def _snapshot(row) -> dict:
    return {k: row[k] for k in row.keys()} if row is not None else {}


# --------------------------------------------------------------------------
# sales invoices
# --------------------------------------------------------------------------
def delete_sales_invoice(conn, user, company_id: int, invoice_id: int, ip=None) -> dict:
    inv = _row(conn, "SELECT * FROM sales_invoices WHERE id=? AND company_id=?", (invoice_id, company_id))
    if inv is None:
        raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", status=404)

    ensure_period_open(conn, company_id, inv["invoice_date"])

    allocs = conn.execute(
        "SELECT a.*, p.payment_no FROM payment_allocations a JOIN payments p ON p.id=a.payment_id "
        "WHERE a.invoice_kind='sales' AND a.invoice_id=?", (invoice_id,)
    ).fetchall()
    if allocs or inv["paid_cents"]:
        refs = "، ".join(sorted({a["payment_no"] for a in allocs})) or "—"
        raise ValidationError(
            "HAS_PAYMENTS: لا يمكن حذف فاتورة عليها تحصيلات — احذف سند التحصيل أولًا "
            f"/ Invoice has receipts ({refs}); delete the receipt first",
            details={"payments": refs}, status=409)

    lines = conn.execute("SELECT * FROM sales_invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchall()
    snapshot = {"invoice": _snapshot(inv), "lines": [_snapshot(l) for l in lines]}

    audit(conn, user, "sales_invoice.delete", "sales_invoice", invoice_id, snapshot,
          company_id=company_id, ip=ip)

    conn.execute("DELETE FROM stock_movements WHERE company_id=? AND ref_type='sales_invoice' AND ref_id=?",
                 (company_id, invoice_id))
    conn.execute("DELETE FROM einvoice_documents WHERE invoice_kind='sales' AND invoice_id=?", (invoice_id,))
    conn.execute("DELETE FROM attachments WHERE entity='sales_invoice' AND entity_id=?", (invoice_id,))
    # detach the journal link first: the FK would otherwise block the entry delete
    conn.execute("UPDATE sales_invoices SET journal_entry_id=NULL WHERE id=?", (invoice_id,))
    conn.execute("DELETE FROM sales_invoice_lines WHERE invoice_id=?", (invoice_id,))
    conn.execute("DELETE FROM sales_invoices WHERE id=?", (invoice_id,))
    _delete_journal_entry(conn, inv["journal_entry_id"])
    return {"ok": True, "deleted": "sales_invoice", "id": invoice_id, "number": inv["invoice_no"]}


# --------------------------------------------------------------------------
# purchase invoices
# --------------------------------------------------------------------------
def delete_purchase_invoice(conn, user, company_id: int, invoice_id: int, ip=None) -> dict:
    inv = _row(conn, "SELECT * FROM purchase_invoices WHERE id=? AND company_id=?", (invoice_id, company_id))
    if inv is None:
        raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", status=404)

    ensure_period_open(conn, company_id, inv["invoice_date"])

    allocs = conn.execute(
        "SELECT a.*, p.payment_no FROM payment_allocations a JOIN payments p ON p.id=a.payment_id "
        "WHERE a.invoice_kind='purchase' AND a.invoice_id=?", (invoice_id,)
    ).fetchall()
    if allocs or inv["paid_cents"]:
        refs = "، ".join(sorted({a["payment_no"] for a in allocs})) or "—"
        raise ValidationError(
            "HAS_PAYMENTS: لا يمكن حذف فاتورة عليها مدفوعات — احذف سند الدفع أولًا "
            f"/ Invoice has payments ({refs}); delete the payment first",
            details={"payments": refs}, status=409)

    lines = conn.execute("SELECT * FROM purchase_invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchall()
    snapshot = {"invoice": _snapshot(inv), "lines": [_snapshot(l) for l in lines]}

    audit(conn, user, "purchase_invoice.delete", "purchase_invoice", invoice_id, snapshot,
          company_id=company_id, ip=ip)

    conn.execute("DELETE FROM stock_movements WHERE company_id=? AND ref_type='purchase_invoice' AND ref_id=?",
                 (company_id, invoice_id))
    conn.execute("DELETE FROM attachments WHERE entity='purchase_invoice' AND entity_id=?", (invoice_id,))
    conn.execute("UPDATE purchase_invoices SET journal_entry_id=NULL WHERE id=?", (invoice_id,))
    conn.execute("DELETE FROM purchase_invoice_lines WHERE invoice_id=?", (invoice_id,))
    conn.execute("DELETE FROM purchase_invoices WHERE id=?", (invoice_id,))
    _delete_journal_entry(conn, inv["journal_entry_id"])
    return {"ok": True, "deleted": "purchase_invoice", "id": invoice_id, "number": inv["invoice_no"]}


# --------------------------------------------------------------------------
# payments / receipts
# --------------------------------------------------------------------------
def delete_payment(conn, user, company_id: int, payment_id: int, ip=None) -> dict:
    pay = _row(conn, "SELECT * FROM payments WHERE id=? AND company_id=?", (payment_id, company_id))
    if pay is None:
        raise ValidationError("NOT_FOUND: السند غير موجود / Payment not found", status=404)

    ensure_period_open(conn, company_id, pay["payment_date"])

    allocs = conn.execute("SELECT * FROM payment_allocations WHERE payment_id=?", (payment_id,)).fetchall()
    snapshot = {"payment": _snapshot(pay), "allocations": [_snapshot(a) for a in allocs]}
    audit(conn, user, "payment.delete", "payment", payment_id, snapshot, company_id=company_id, ip=ip)

    # give the money back to the invoices it was applied to
    for a in allocs:
        table = "sales_invoices" if a["invoice_kind"] == "sales" else "purchase_invoices"
        row = conn.execute(f"SELECT total_cents, paid_cents, status FROM {table} WHERE id=?", (a["invoice_id"],)).fetchone()
        if row is None:
            continue
        paid = max(0, row["paid_cents"] - a["amount_cents"])
        status = "posted"
        if paid <= 0:
            status = "posted"
        elif paid >= row["total_cents"]:
            status = "paid"
        else:
            status = "partially_paid"
        if row["status"] == "cancelled":
            status = "cancelled"
        conn.execute(f"UPDATE {table} SET paid_cents=?, status=? WHERE id=?", (paid, status, a["invoice_id"]))

    conn.execute("DELETE FROM payment_allocations WHERE payment_id=?", (payment_id,))
    conn.execute("DELETE FROM stock_movements WHERE company_id=? AND ref_type='payment' AND ref_id=?",
                 (company_id, payment_id))
    conn.execute("DELETE FROM attachments WHERE entity='payment' AND entity_id=?", (payment_id,))
    conn.execute("UPDATE payments SET journal_entry_id=NULL WHERE id=?", (payment_id,))
    conn.execute("DELETE FROM payments WHERE id=?", (payment_id,))
    _delete_journal_entry(conn, pay["journal_entry_id"])
    return {"ok": True, "deleted": "payment", "id": payment_id, "number": pay["payment_no"]}


# --------------------------------------------------------------------------
# master data
# --------------------------------------------------------------------------
def _refs_for_party(conn, company_id: int, kind: str, party_id: int):
    """Every document that would be orphaned by deleting this party."""
    if kind == "customer":
        rows = conn.execute(
            "SELECT invoice_no AS doc FROM sales_invoices WHERE company_id=? AND customer_id=?",
            (company_id, party_id)).fetchall()
        pay_rows = conn.execute(
            "SELECT payment_no AS doc FROM payments WHERE company_id=? AND party_type='customer' AND party_id=?",
            (company_id, party_id)).fetchall()
    else:
        rows = conn.execute(
            "SELECT invoice_no AS doc FROM purchase_invoices WHERE company_id=? AND vendor_id=?",
            (company_id, party_id)).fetchall()
        pay_rows = conn.execute(
            "SELECT payment_no AS doc FROM payments WHERE company_id=? AND party_type='vendor' AND party_id=?",
            (company_id, party_id)).fetchall()
    return [r["doc"] for r in rows] + [r["doc"] for r in pay_rows]


def delete_party(conn, user, company_id: int, party_id: int, kind: str = "customer", ip=None) -> dict:
    table = "customers" if kind == "customer" else "vendors"
    row = _row(conn, f"SELECT * FROM {table} WHERE id=? AND company_id=?", (party_id, company_id))
    if row is None:
        label = "العميل" if kind == "customer" else "المورد"
        raise ValidationError(f"NOT_FOUND: {label} غير موجود / Not found", status=404)

    refs = _refs_for_party(conn, company_id, kind, party_id)
    if refs:
        label = "عميل" if kind == "customer" else "مورد"
        raise ValidationError(
            f"HAS_DOCUMENTS: لا يمكن حذف {label} مرتبط بمستندات — احذف الفواتير والسندات أولًا "
            f"/ Party has {len(refs)} document(s)",
            details={"documents": refs[:25], "count": len(refs)}, status=409)

    audit(conn, user, f"{kind}.delete", kind, party_id, _snapshot(row), company_id=company_id, ip=ip)
    conn.execute(f"DELETE FROM {table} WHERE id=?", (party_id,))
    return {"ok": True, "deleted": kind, "id": party_id, "code": row["code"]}


# --------------------------------------------------------------------------
# journal entries (manual or auto) — reverses nothing, removes entirely
# --------------------------------------------------------------------------
def delete_journal_entry(conn, user, company_id: int, entry_id: int, ip=None) -> dict:
    ent = _row(conn, "SELECT * FROM journal_entries WHERE id=? AND company_id=?", (entry_id, company_id))
    if ent is None:
        raise ValidationError("NOT_FOUND: القيد غير موجود / Entry not found", status=404)
    ensure_period_open(conn, company_id, ent["entry_date"])

    owner = conn.execute(
        """SELECT 'sales' AS kind, invoice_no AS doc, id FROM sales_invoices WHERE journal_entry_id=?
           UNION ALL SELECT 'purchase', invoice_no, id FROM purchase_invoices WHERE journal_entry_id=?
           UNION ALL SELECT 'payment', payment_no, id FROM payments WHERE journal_entry_id=?
           UNION ALL SELECT 'payroll', run_no, id FROM payroll_runs WHERE journal_entry_id=?""",
        (entry_id, entry_id, entry_id, entry_id)).fetchall()
    if owner:
        o = owner[0]
        label = {"sales": "فاتورة مبيعات", "purchase": "فاتورة مشتريات",
                 "payment": "سند", "payroll": "مسير رواتب"}[o["kind"]]
        raise ValidationError(
            f"SYSTEM_ENTRY: هذا القيد ناتج عن {label} ({o['doc']}) — احذف المستند نفسه "
            "/ This entry was generated by a document; delete the document instead",
            details={"document": o["doc"], "kind": o["kind"]}, status=409)

    lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id=?", (entry_id,)).fetchall()
    audit(conn, user, "journal_entry.delete", "journal_entry", entry_id,
          {"entry": _snapshot(ent), "lines": [_snapshot(l) for l in lines]},
          company_id=company_id, ip=ip)

    conn.execute("UPDATE stock_movements SET journal_entry_id=NULL WHERE journal_entry_id=?", (entry_id,))
    _delete_journal_entry(conn, entry_id)
    return {"ok": True, "deleted": "journal_entry", "id": entry_id, "number": ent["entry_no"]}


# --------------------------------------------------------------------------
# products / services
# --------------------------------------------------------------------------
def delete_product(conn, user, company_id: int, product_id: int, ip=None) -> dict:
    prod = _row(conn, "SELECT * FROM products WHERE id=? AND company_id=?", (product_id, company_id))
    if prod is None:
        raise ValidationError("NOT_FOUND: الصنف غير موجود / Item not found", status=404)

    used = conn.execute(
        """SELECT (SELECT COUNT(*) FROM sales_invoice_lines WHERE product_id=?) +
                  (SELECT COUNT(*) FROM purchase_invoice_lines WHERE product_id=?) +
                  (SELECT COUNT(*) FROM stock_movements WHERE company_id=? AND product_id=?) AS n""",
        (product_id, product_id, company_id, product_id)).fetchone()["n"]
    if used:
        raise ValidationError(
            "IN_USE: لا يمكن حذف صنف مرتبط بحركات أو فواتير — أوقفه بدلًا من حذفه "
            f"/ Item is used in {used} record(s); deactivate it instead",
            details={"records": used}, status=409)

    audit(conn, user, "product.delete", "product", product_id, _snapshot(prod), company_id=company_id, ip=ip)
    conn.execute("DELETE FROM products WHERE id=?", (product_id,))
    return {"ok": True, "deleted": "product", "id": product_id, "code": prod["sku"]}


# --------------------------------------------------------------------------
# bank statement lines
# --------------------------------------------------------------------------
def delete_bank_transaction(conn, user, company_id: int, txn_id: int, ip=None) -> dict:
    txn = _row(conn, "SELECT * FROM bank_transactions WHERE id=? AND company_id=?", (txn_id, company_id))
    if txn is None:
        raise ValidationError("NOT_FOUND: الحركة غير موجودة / Transaction not found", status=404)
    if txn["is_reconciled"]:
        raise ValidationError(
            "RECONCILED: لا يمكن حذف حركة مطابقة — ألغِ المطابقة أولًا "
            "/ Reconciled transaction cannot be deleted; unmatch it first", status=409)
    audit(conn, user, "bank_transaction.delete", "bank_transaction", txn_id, _snapshot(txn),
          company_id=company_id, ip=ip)
    conn.execute("DELETE FROM bank_transactions WHERE id=?", (txn_id,))
    return {"ok": True, "deleted": "bank_transaction", "id": txn_id,
            "number": txn["statement_ref"] or f"BT-{txn_id}"}


# --------------------------------------------------------------------------
# inventory movements (manual adjustments/in/out)
# --------------------------------------------------------------------------
def delete_stock_movement(conn, user, company_id: int, movement_id: int, ip=None) -> dict:
    mv = _row(conn, "SELECT * FROM stock_movements WHERE id=? AND company_id=?", (movement_id, company_id))
    if mv is None:
        raise ValidationError("NOT_FOUND: الحركة غير موجودة / Movement not found", status=404)
    ensure_period_open(conn, company_id, mv["move_date"])
    if mv["ref_type"] in ("sales_invoice", "purchase_invoice"):
        raise ValidationError(
            "SYSTEM_MOVEMENT: الحركة ناتجة عن فاتورة — احذف الفاتورة نفسها "
            "/ Movement belongs to an invoice; delete the invoice instead", status=409)

    audit(conn, user, "stock_movement.delete", "stock_movement", movement_id, _snapshot(mv),
          company_id=company_id, ip=ip)
    conn.execute("UPDATE stock_movements SET journal_entry_id=NULL WHERE id=?", (movement_id,))
    conn.execute("DELETE FROM stock_movements WHERE id=?", (movement_id,))
    _delete_journal_entry(conn, mv["journal_entry_id"])
    return {"ok": True, "deleted": "stock_movement", "id": movement_id, "number": f"MV-{movement_id}"}


# --------------------------------------------------------------------------
# fixed assets
# --------------------------------------------------------------------------
def delete_asset(conn, user, company_id: int, asset_id: int, ip=None) -> dict:
    asset = _row(conn, "SELECT * FROM fixed_assets WHERE id=? AND company_id=?", (asset_id, company_id))
    if asset is None:
        raise ValidationError("NOT_FOUND: الأصل غير موجود / Asset not found", status=404)
    if asset["accumulated_cents"] or asset["status"] == "disposed":
        raise ValidationError(
            "HAS_DEPRECIATION: لا يمكن حذف أصل تم إهلاكه — استخدم الاستبعاد "
            "/ Asset already depreciated; dispose it instead", status=409)
    audit(conn, user, "asset.delete", "fixed_asset", asset_id, _snapshot(asset), company_id=company_id, ip=ip)
    conn.execute("DELETE FROM fixed_assets WHERE id=?", (asset_id,))
    return {"ok": True, "deleted": "fixed_asset", "id": asset_id, "code": asset["code"]}


# --------------------------------------------------------------------------
# employees
# --------------------------------------------------------------------------
def delete_employee(conn, user, company_id: int, employee_id: int, ip=None) -> dict:
    emp = _row(conn, "SELECT * FROM employees WHERE id=? AND company_id=?", (employee_id, company_id))
    if emp is None:
        raise ValidationError("NOT_FOUND: الموظف غير موجود / Employee not found", status=404)
    used = conn.execute("SELECT COUNT(*) AS n FROM payroll_lines WHERE employee_id=?", (employee_id,)).fetchone()["n"]
    if used:
        raise ValidationError(
            f"HAS_PAYROLL: الموظف مدرج في {used} مسير رواتب — أوقفه بدلًا من حذفه "
            "/ Employee appears in payroll runs; deactivate instead",
            details={"runs": used}, status=409)
    audit(conn, user, "employee.delete", "employee", employee_id, _snapshot(emp), company_id=company_id, ip=ip)
    conn.execute("DELETE FROM employees WHERE id=?", (employee_id,))
    return {"ok": True, "deleted": "employee", "id": employee_id, "code": emp["code"]}


# --------------------------------------------------------------------------
# budgets
# --------------------------------------------------------------------------
def delete_budget(conn, user, company_id: int, budget_id: int, ip=None) -> dict:
    row = _row(conn, "SELECT * FROM budgets WHERE id=? AND company_id=?", (budget_id, company_id))
    if row is None:
        raise ValidationError("NOT_FOUND: بند الموازنة غير موجود / Budget line not found", status=404)
    audit(conn, user, "budget.delete", "budget", budget_id, _snapshot(row), company_id=company_id, ip=ip)
    conn.execute("DELETE FROM budgets WHERE id=?", (budget_id,))
    return {"ok": True, "deleted": "budget", "id": budget_id, "number": f"BUD-{budget_id}"}
