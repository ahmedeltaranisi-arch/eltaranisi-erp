"""Operational documents: sales, purchases, payments, depreciation, payroll.

Each posting function is idempotent, runs inside the caller's DB transaction and
creates the correct double-entry impact through accounting.auto_post_entry().
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import accounting
from accounting import ValidationError, account_id_by_code
from security import audit, notify, utcnow

# Standard account codes used by the posting rules (configured per company in settings)
ACC = {
    "ar": "1210",
    "vat_input": "1260",
    "inventory": "1310",
    "ap": "2110",
    "vat_output": "2210",
    "payroll_payable": "2130",
    "deductions_payable": "2135",
    "revenue": "4100",
    "cogs": "5100",
    "salary_expense": "5200",
    "dep_expense": "5320",
    "accum_dep": "1330",
}


def _qty(qty_milli: int, price_cents: int) -> int:
    return int((Decimal(qty_milli) * Decimal(price_cents) / Decimal(1000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_totals(lines) -> dict:
    subtotal = discount = tax = total = 0
    computed = []
    for i, ln in enumerate(lines, start=1):
        qty = int(ln.get("qty_milli") or 0)
        price = int(ln.get("unit_price_cents") or 0)
        if qty <= 0:
            raise ValidationError("INVALID_QTY: الكمية يجب أن تكون أكبر من صفر / Quantity must be positive")
        if price < 0:
            raise ValidationError("INVALID_PRICE: السعر غير صالح / Invalid price")
        gross = _qty(qty, price)
        line_disc = int(ln.get("discount_cents") or 0)
        if line_disc > gross:
            raise ValidationError("INVALID_DISCOUNT: الخصم أكبر من قيمة السطر / Discount exceeds line value")
        net = gross - line_disc
        rate = int(ln.get("tax_rate_bp") or 0)
        line_tax = int((Decimal(net) * Decimal(rate) / Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        computed.append({**ln, "line_no": i, "qty_milli": qty, "unit_price_cents": price,
                         "discount_cents": line_disc, "tax_rate_bp": rate,
                         "line_subtotal_cents": net, "line_tax_cents": line_tax,
                         "line_total_cents": net + line_tax})
        subtotal += net
        discount += line_disc
        tax += line_tax
        total += net + line_tax
    return {"lines": computed, "subtotal": subtotal, "discount": discount, "tax": tax, "total": total}


# --------------------------------------------------------------------------
# Sales invoices
# --------------------------------------------------------------------------
def create_sales_invoice(conn, user, company_id, payload) -> int:
    customer = conn.execute("SELECT * FROM customers WHERE id=? AND company_id=?",
                            (payload["customer_id"], company_id)).fetchone()
    if customer is None:
        raise ValidationError("NOT_FOUND: العميل غير موجود / Customer not found")
    totals = compute_totals(payload.get("lines") or [])
    # Stocked products require a warehouse so the stock & COGS impact can be recorded
    if not payload.get("warehouse_id"):
        for ln in totals["lines"]:
            if ln.get("product_id"):
                prod = conn.execute("SELECT is_service FROM products WHERE id=?", (ln["product_id"],)).fetchone()
                if prod and not prod["is_service"]:
                    raise ValidationError(
                        "WAREHOUSE_REQUIRED: يجب تحديد المستودع لفاتورة تحتوي أصنافًا مخزنية / "
                        "A warehouse is required when the invoice includes stocked products")
    inv_no = payload.get("invoice_no") or accounting.next_number(conn, company_id, "sales_invoice", payload["invoice_date"])
    dup = conn.execute("SELECT id FROM sales_invoices WHERE company_id=? AND invoice_no=?", (company_id, inv_no)).fetchone()
    if dup:
        raise ValidationError("DUPLICATE: رقم الفاتورة مستخدم بالفعل / Invoice number already used")

    # Credit limit check (posted + draft exposure)
    if customer["credit_limit_cents"]:
        exposure = conn.execute(
            """SELECT COALESCE(SUM(total_cents - paid_cents),0) v FROM sales_invoices
               WHERE customer_id=? AND status IN ('posted','partially_paid')""",
            (payload["customer_id"],),
        ).fetchone()["v"]
        if exposure + totals["total"] > customer["credit_limit_cents"]:
            raise ValidationError(
                "CREDIT_LIMIT: تجاوز حد الائتمان للعميل / Customer credit limit exceeded",
                {"limit": customer["credit_limit_cents"] / 100, "exposure": exposure / 100,
                 "invoice": totals["total"] / 100},
            )

    cur = conn.execute(
        """INSERT INTO sales_invoices (company_id, branch_id, invoice_no, customer_id, invoice_date, due_date,
              warehouse_id, subtotal_cents, discount_cents, tax_cents, total_cents, status, notes,
              idempotency_key, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?)""",
        (company_id, payload.get("branch_id"), inv_no, payload["customer_id"], payload["invoice_date"],
         payload.get("due_date") or payload["invoice_date"], payload.get("warehouse_id"),
         totals["subtotal"], totals["discount"], totals["tax"], totals["total"],
         payload.get("notes"), payload.get("idempotency_key"), user["id"], utcnow()),
    )
    inv_id = cur.lastrowid
    for ln in totals["lines"]:
        conn.execute(
            """INSERT INTO sales_invoice_lines (invoice_id, line_no, product_id, description, qty_milli,
                 unit_price_cents, discount_cents, tax_rate_bp, line_subtotal_cents, line_tax_cents, line_total_cents)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (inv_id, ln["line_no"], ln.get("product_id"), ln.get("description"), ln["qty_milli"],
             ln["unit_price_cents"], ln["discount_cents"], ln["tax_rate_bp"],
             ln["line_subtotal_cents"], ln["line_tax_cents"], ln["line_total_cents"]),
        )
    audit(conn, user, "sales_invoice.create", "sales_invoice", inv_id,
          {"invoice_no": inv_no, "total": totals["total"] / 100}, company_id=company_id)
    return inv_id


def post_sales_invoice(conn, user, company_id, invoice_id: int) -> dict:
    inv = conn.execute("SELECT * FROM sales_invoices WHERE id=? AND company_id=?", (invoice_id, company_id)).fetchone()
    if inv is None:
        raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found")
    if inv["status"] != "draft":
        raise ValidationError("INVALID_STATE: الفاتورة مرحّلة بالفعل / Invoice already posted")
    accounting.ensure_period_open(conn, company_id, inv["invoice_date"])
    lines = conn.execute("SELECT * FROM sales_invoice_lines WHERE invoice_id=? ORDER BY line_no", (invoice_id,)).fetchall()

    ar = accounting.customer_ar_account(conn, company_id, inv["customer_id"])
    vat = account_id_by_code(conn, company_id, ACC["vat_output"])
    je_lines = [{"account_id": ar, "debit_cents": inv["total_cents"], "credit_cents": 0,
                 "customer_id": inv["customer_id"],
                 "description": f"فاتورة بيع {inv['invoice_no']}"}]
    if inv["tax_cents"]:
        je_lines.append({"account_id": vat, "debit_cents": 0, "credit_cents": inv["tax_cents"],
                         "description": f"ض.ق.م {inv['invoice_no']}"})
    revenue_total = 0
    cogs_total = 0
    cogs_lines = []
    for ln in lines:
        prod = conn.execute("SELECT * FROM products WHERE id=?", (ln["product_id"],)).fetchone() if ln["product_id"] else None
        rev_acc = (prod["revenue_account_id"] if prod and prod["revenue_account_id"] else None) \
            or account_id_by_code(conn, company_id, ACC["revenue"])
        je_lines.append({"account_id": rev_acc, "debit_cents": 0, "credit_cents": ln["line_subtotal_cents"],
                         "description": ln["description"] or inv["invoice_no"]})
        revenue_total += ln["line_subtotal_cents"]
        if prod and not prod["is_service"] and inv["warehouse_id"]:
            cogs_acc = prod["cogs_account_id"] or account_id_by_code(conn, company_id, ACC["cogs"])
            inv_acc = prod["inventory_account_id"] or account_id_by_code(conn, company_id, ACC["inventory"])
            value, avg = accounting.stock_out(
                conn, user, company_id, prod["id"], inv["warehouse_id"], ln["qty_milli"],
                inv["invoice_date"], ref_type="sales_invoice", ref_id=invoice_id,
            )
            if value:
                cogs_lines.append({"account_id": cogs_acc, "debit_cents": value, "credit_cents": 0,
                                   "description": f"تكلفة مبيعات {inv['invoice_no']}"})
                cogs_lines.append({"account_id": inv_acc, "debit_cents": 0, "credit_cents": value,
                                   "description": f"صرف مخزون {inv['invoice_no']}"})
                cogs_total += value

    je_id = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=inv["invoice_date"],
        memo=f"فاتورة مبيعات {inv['invoice_no']}", lines=je_lines,
        entry_type="sales", branch_id=inv["branch_id"],
        source_type="sales_invoice", source_id=invoice_id,
        idempotency_key=f"sales_invoice:{invoice_id}",
    )
    if cogs_lines:
        cogs_je = accounting.auto_post_entry(
            conn, user, company_id=company_id, entry_date=inv["invoice_date"],
            memo=f"تكلفة مبيعات {inv['invoice_no']}", lines=cogs_lines,
            entry_type="sales_cogs", branch_id=inv["branch_id"],
            source_type="sales_invoice_cogs", source_id=invoice_id,
            idempotency_key=f"sales_invoice_cogs:{invoice_id}",
        )
        conn.execute("UPDATE stock_movements SET journal_entry_id=? WHERE ref_type='sales_invoice' AND ref_id=?",
                     (cogs_je, invoice_id))

    conn.execute("UPDATE sales_invoices SET status='posted', journal_entry_id=?, posted_at=? WHERE id=?",
                 (je_id, utcnow(), invoice_id))
    audit(conn, user, "sales_invoice.post", "sales_invoice", invoice_id,
          {"journal_entry_id": je_id, "cogs": cogs_total / 100}, company_id=company_id)
    return {"journal_entry_id": je_id, "cogs_cents": cogs_total}


# --------------------------------------------------------------------------
# Purchase invoices
# --------------------------------------------------------------------------
def create_purchase_invoice(conn, user, company_id, payload) -> int:
    vendor = conn.execute("SELECT * FROM vendors WHERE id=? AND company_id=?",
                          (payload["vendor_id"], company_id)).fetchone()
    if vendor is None:
        raise ValidationError("NOT_FOUND: المورد غير موجود / Vendor not found")
    if payload.get("supplier_ref"):
        dup = conn.execute(
            "SELECT id FROM purchase_invoices WHERE company_id=? AND vendor_id=? AND supplier_ref=? AND status<>'cancelled'",
            (company_id, payload["vendor_id"], payload["supplier_ref"])).fetchone()
        if dup:
            raise ValidationError("DUPLICATE: فاتورة مورد مكررة بنفس الرقم المرجعي / Duplicate vendor invoice reference")
    totals = compute_totals(payload.get("lines") or [])
    inv_no = payload.get("invoice_no") or accounting.next_number(conn, company_id, "purchase_invoice", payload["invoice_date"])
    three_way = 1 if (payload.get("po_number") and payload.get("grn_number")) else 0
    cur = conn.execute(
        """INSERT INTO purchase_invoices (company_id, branch_id, invoice_no, supplier_ref, vendor_id, invoice_date,
              due_date, warehouse_id, po_number, grn_number, subtotal_cents, discount_cents, tax_cents, total_cents,
              status, three_way_match, notes, idempotency_key, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?)""",
        (company_id, payload.get("branch_id"), inv_no, payload.get("supplier_ref"), payload["vendor_id"],
         payload["invoice_date"], payload.get("due_date") or payload["invoice_date"], payload.get("warehouse_id"),
         payload.get("po_number"), payload.get("grn_number"), totals["subtotal"], totals["discount"],
         totals["tax"], totals["total"], three_way, payload.get("notes"),
         payload.get("idempotency_key"), user["id"], utcnow()),
    )
    inv_id = cur.lastrowid
    for ln in totals["lines"]:
        conn.execute(
            """INSERT INTO purchase_invoice_lines (invoice_id, line_no, product_id, description, qty_milli,
                 unit_price_cents, discount_cents, tax_rate_bp, line_subtotal_cents, line_tax_cents, line_total_cents)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (inv_id, ln["line_no"], ln.get("product_id"), ln.get("description"), ln["qty_milli"],
             ln["unit_price_cents"], ln["discount_cents"], ln["tax_rate_bp"],
             ln["line_subtotal_cents"], ln["line_tax_cents"], ln["line_total_cents"]),
        )
    audit(conn, user, "purchase_invoice.create", "purchase_invoice", inv_id,
          {"invoice_no": inv_no, "total": totals["total"] / 100}, company_id=company_id)
    return inv_id


def post_purchase_invoice(conn, user, company_id, invoice_id: int) -> dict:
    inv = conn.execute("SELECT * FROM purchase_invoices WHERE id=? AND company_id=?", (invoice_id, company_id)).fetchone()
    if inv is None:
        raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found")
    if inv["status"] != "draft":
        raise ValidationError("INVALID_STATE: الفاتورة مرحّلة بالفعل / Invoice already posted")
    accounting.ensure_period_open(conn, company_id, inv["invoice_date"])
    lines = conn.execute("SELECT * FROM purchase_invoice_lines WHERE invoice_id=? ORDER BY line_no", (invoice_id,)).fetchall()
    ap = accounting.vendor_ap_account(conn, company_id, inv["vendor_id"])
    je_lines = []
    inv_total = 0
    for ln in lines:
        prod = conn.execute("SELECT * FROM products WHERE id=?", (ln["product_id"],)).fetchone() if ln["product_id"] else None
        debit_acc = (prod["inventory_account_id"] if prod and prod["inventory_account_id"] else None) \
            or account_id_by_code(conn, company_id, ACC["inventory"])
        je_lines.append({"account_id": debit_acc, "debit_cents": ln["line_subtotal_cents"], "credit_cents": 0,
                         "description": ln["description"] or inv["invoice_no"]})
        inv_total += ln["line_subtotal_cents"]
        if prod and not prod["is_service"] and inv["warehouse_id"]:
            accounting.stock_in(conn, user, company_id, prod["id"], inv["warehouse_id"],
                                ln["qty_milli"], ln["unit_price_cents"], inv["invoice_date"],
                                ref_type="purchase_invoice", ref_id=invoice_id,
                                notes=f"فاتورة شراء {inv['invoice_no']}")
    if inv["tax_cents"]:
        je_lines.append({"account_id": account_id_by_code(conn, company_id, ACC["vat_input"]),
                         "debit_cents": inv["tax_cents"], "credit_cents": 0,
                         "description": f"ض.ق.م مشتريات {inv['invoice_no']}"})
    je_lines.append({"account_id": ap, "debit_cents": 0, "credit_cents": inv["total_cents"],
                     "vendor_id": inv["vendor_id"], "description": f"فاتورة شراء {inv['invoice_no']}"})
    je_id = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=inv["invoice_date"],
        memo=f"فاتورة مشتريات {inv['invoice_no']}", lines=je_lines, entry_type="purchase",
        branch_id=inv["branch_id"], source_type="purchase_invoice", source_id=invoice_id,
        idempotency_key=f"purchase_invoice:{invoice_id}",
    )
    conn.execute("UPDATE purchase_invoices SET status='posted', journal_entry_id=?, posted_at=? WHERE id=?",
                 (je_id, utcnow(), invoice_id))
    conn.execute("UPDATE stock_movements SET journal_entry_id=? WHERE ref_type='purchase_invoice' AND ref_id=?",
                 (je_id, invoice_id))
    audit(conn, user, "purchase_invoice.post", "purchase_invoice", invoice_id,
          {"journal_entry_id": je_id}, company_id=company_id)
    return {"journal_entry_id": je_id}


# --------------------------------------------------------------------------
# Payments & receipts
# --------------------------------------------------------------------------
def create_payment(conn, user, company_id, payload) -> int:
    amount = int(payload["amount_cents"])
    if amount <= 0:
        raise ValidationError("INVALID_AMOUNT: المبلغ يجب أن يكون موجبًا / Amount must be positive")
    ptype = payload["payment_type"]
    gl = conn.execute("SELECT id, company_id FROM accounts WHERE id=?", (payload["gl_account_id"],)).fetchone()
    if gl is None or gl["company_id"] != company_id:
        raise ValidationError("INVALID_ACCOUNT: حساب الصندوق/البنك غير صالح / Invalid cash/bank account")
    doc_type = "receipt" if ptype == "receipt" else "payment"
    pay_no = payload.get("payment_no") or accounting.next_number(conn, company_id, doc_type, payload["payment_date"])
    cur = conn.execute(
        """INSERT INTO payments (company_id, branch_id, payment_no, payment_type, payment_date, party_type,
              party_id, method, gl_account_id, amount_cents, status, reference, notes, idempotency_key,
              created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?)""",
        (company_id, payload.get("branch_id"), pay_no, ptype, payload["payment_date"],
         payload.get("party_type"), payload.get("party_id"), payload["method"], payload["gl_account_id"],
         amount, payload.get("reference"), payload.get("notes"), payload.get("idempotency_key"),
         user["id"], utcnow()),
    )
    pay_id = cur.lastrowid
    for alloc in payload.get("allocations") or []:
        amt = int(alloc["amount_cents"])
        kind = alloc["invoice_kind"]
        table = "sales_invoices" if kind == "sales" else "purchase_invoices"
        inv = conn.execute(f"SELECT id, total_cents, paid_cents, status FROM {table} WHERE id=? AND company_id=?",
                           (alloc["invoice_id"], company_id)).fetchone()
        if inv is None:
            raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found")
        if inv["status"] not in ("posted", "partially_paid"):
            raise ValidationError("INVALID_STATE: لا يمكن تخصيص دفعة لفاتورة غير مرحّلة / Invoice not posted")
        if amt > inv["total_cents"] - inv["paid_cents"]:
            raise ValidationError("OVER_ALLOCATION: مبلغ التخصيص أكبر من المتبقي / Allocation exceeds invoice balance")
        conn.execute("INSERT INTO payment_allocations (payment_id, invoice_kind, invoice_id, amount_cents) VALUES (?,?,?,?)",
                     (pay_id, kind, inv["id"], amt))
    allocated = conn.execute("SELECT COALESCE(SUM(amount_cents),0) v FROM payment_allocations WHERE payment_id=?",
                            (pay_id,)).fetchone()["v"]
    if allocated > amount:
        raise ValidationError("OVER_ALLOCATION: إجمالي التخصيص أكبر من مبلغ الدفعة / Allocations exceed payment amount")
    conn.execute("UPDATE payments SET allocated_cents=? WHERE id=?", (allocated, pay_id))
    audit(conn, user, f"payment.create", "payment", pay_id,
          {"payment_no": pay_no, "amount": amount / 100}, company_id=company_id)
    return pay_id


def post_payment(conn, user, company_id, payment_id: int) -> dict:
    p = conn.execute("SELECT * FROM payments WHERE id=? AND company_id=?", (payment_id, company_id)).fetchone()
    if p is None:
        raise ValidationError("NOT_FOUND: السند غير موجود / Voucher not found")
    if p["status"] != "draft":
        raise ValidationError("INVALID_STATE: السند مرحّل بالفعل / Voucher already posted")
    accounting.ensure_period_open(conn, company_id, p["payment_date"])
    allocs = conn.execute("SELECT * FROM payment_allocations WHERE payment_id=?", (payment_id,)).fetchall()
    remaining = p["amount_cents"] - sum(a["amount_cents"] for a in allocs)

    je_lines = []
    if p["payment_type"] == "receipt":
        customer_id = p["party_id"] if p["party_type"] == "customer" else None
        je_lines.append({"account_id": p["gl_account_id"], "debit_cents": p["amount_cents"], "credit_cents": 0,
                         "description": f"تحصيل {p['payment_no']}"})
        ar = accounting.customer_ar_account(conn, company_id, customer_id) if customer_id else \
            account_id_by_code(conn, company_id, ACC["ar"])
        je_lines.append({"account_id": ar, "debit_cents": 0, "credit_cents": p["amount_cents"],
                         "customer_id": customer_id, "description": f"تحصيل {p['payment_no']}"})
    elif p["payment_type"] in ("payment", "expense", "employee_advance"):
        vendor_id = p["party_id"] if p["party_type"] == "vendor" else None
        je_lines.append({"account_id": p["gl_account_id"], "debit_cents": 0, "credit_cents": p["amount_cents"],
                         "description": f"صرف {p['payment_no']}"})
        ap = accounting.vendor_ap_account(conn, company_id, vendor_id) if vendor_id else \
            account_id_by_code(conn, company_id, ACC["ap"])
        je_lines.append({"account_id": ap, "debit_cents": p["amount_cents"], "credit_cents": 0,
                         "vendor_id": vendor_id, "description": f"صرف {p['payment_no']}"})
    else:
        raise ValidationError("INVALID_TYPE: نوع السند غير معروف / Unknown voucher type")

    je_id = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=p["payment_date"],
        memo=f"{'سند قبض' if p['payment_type'] == 'receipt' else 'سند صرف'} {p['payment_no']}",
        lines=je_lines, entry_type=p["payment_type"], branch_id=p["branch_id"],
        source_type="payment", source_id=payment_id,
        idempotency_key=f"payment:{payment_id}",
    )
    # Apply allocations to invoice balances
    for a in allocs:
        if a["invoice_kind"] == "sales":
            inv = conn.execute("SELECT * FROM sales_invoices WHERE id=?", (a["invoice_id"],)).fetchone()
            new_paid = inv["paid_cents"] + a["amount_cents"]
            status = "paid" if new_paid >= inv["total_cents"] else "partially_paid"
            conn.execute("UPDATE sales_invoices SET paid_cents=?, status=? WHERE id=?",
                         (new_paid, status, inv["id"]))
        else:
            inv = conn.execute("SELECT * FROM purchase_invoices WHERE id=?", (a["invoice_id"],)).fetchone()
            new_paid = inv["paid_cents"] + a["amount_cents"]
            status = "paid" if new_paid >= inv["total_cents"] else "partially_paid"
            conn.execute("UPDATE purchase_invoices SET paid_cents=?, status=? WHERE id=?",
                         (new_paid, status, inv["id"]))
    conn.execute("UPDATE payments SET status='posted', journal_entry_id=?, posted_at=? WHERE id=?",
                 (je_id, utcnow(), payment_id))
    audit(conn, user, "payment.post", "payment", payment_id,
          {"journal_entry_id": je_id, "allocated": p["allocated_cents"] / 100,
           "unallocated": remaining / 100}, company_id=company_id)
    if remaining:
        notify(conn, company_id, None, "info",
               f"دفعة غير مخصصة {p['payment_no']}", f"Unallocated payment {p['payment_no']}",
               f"مبلغ غير مخصص: {remaining/100:,.2f}", f"Unallocated amount: {remaining/100:,.2f}",
               f"#/payments")
    return {"journal_entry_id": je_id, "unallocated_cents": remaining}


# --------------------------------------------------------------------------
# Fixed assets depreciation
# --------------------------------------------------------------------------
def depreciation_run(conn, user, company_id, period_start: str, period_end: str) -> dict:
    assets = conn.execute(
        "SELECT * FROM fixed_assets WHERE company_id=? AND status='active' AND start_date<=?",
        (company_id, period_end)).fetchall()
    rows = []
    total = 0
    for a in assets:
        monthly = int((a["cost_cents"] - a["salvage_cents"]) / a["useful_life_months"]) if a["useful_life_months"] else 0
        if monthly <= 0:
            continue
        remaining = a["cost_cents"] - a["salvage_cents"] - a["accumulated_cents"]
        amount = min(monthly, max(remaining, 0))
        if amount <= 0:
            continue
        rows.append((a, amount))
        total += amount
    if not rows:
        raise ValidationError("NOTHING_TO_DEPRECIATE: لا توجد أصول تحتاج إهلاكًا في هذه الفترة / No assets to depreciate")
    run_no = accounting.next_number(conn, company_id, "depreciation", period_end)
    cur = conn.execute(
        """INSERT INTO depreciation_runs (company_id, run_no, period_start, period_end, status, total_cents, created_by, created_at)
           VALUES (?,?,?,?,'draft',?,?,?)""",
        (company_id, run_no, period_start, period_end, total, user["id"], utcnow()))
    run_id = cur.lastrowid
    exp_acc = account_id_by_code(conn, company_id, ACC["dep_expense"])
    accum_acc = account_id_by_code(conn, company_id, ACC["accum_dep"])
    je_lines = [{"account_id": exp_acc, "debit_cents": total, "credit_cents": 0,
                 "description": f"إهلاك دوري {period_start} — {period_end}"},
                {"account_id": accum_acc, "debit_cents": 0, "credit_cents": total,
                 "description": f"مجمع إهلاك {period_start} — {period_end}"}]
    je_id = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=period_end,
        memo=f"إهلاك دوري {run_no}", lines=je_lines, entry_type="depreciation",
        source_type="depreciation_run", source_id=run_id, idempotency_key=f"depreciation:{run_id}")
    for a, amount in rows:
        conn.execute("INSERT INTO depreciation_lines (run_id, asset_id, amount_cents) VALUES (?,?,?)",
                     (run_id, a["id"], amount))
        conn.execute("UPDATE fixed_assets SET accumulated_cents = accumulated_cents + ? WHERE id=?", (amount, a["id"]))
    conn.execute("UPDATE depreciation_runs SET status='posted', journal_entry_id=? WHERE id=?", (je_id, run_id))
    audit(conn, user, "assets.depreciation", "depreciation_run", run_id,
          {"run_no": run_no, "total": total / 100, "assets": len(rows)}, company_id=company_id)
    return {"run_id": run_id, "run_no": run_no, "total_cents": total, "journal_entry_id": je_id, "assets": len(rows)}


# --------------------------------------------------------------------------
# Payroll
# --------------------------------------------------------------------------
def payroll_run(conn, user, company_id, period: str) -> dict:
    employees = conn.execute("SELECT * FROM employees WHERE company_id=? AND is_active=1", (company_id,)).fetchall()
    if not employees:
        raise ValidationError("NO_EMPLOYEES: لا يوجد موظفون نشطون / No active employees")
    gross = deductions = net = 0
    lines = []
    for e in employees:
        g = e["basic_salary_cents"] + e["allowances_cents"]
        d = e["deductions_cents"] + e["insurance_cents"] + e["tax_cents"]
        n = g - d
        lines.append((e, g, d, n))
        gross += g
        deductions += d
        net += n
    run_no = accounting.next_number(conn, company_id, "payroll", period + "-01")
    cur = conn.execute(
        """INSERT INTO payroll_runs (company_id, run_no, period, status, gross_cents, deductions_cents, net_cents,
              created_by, created_at) VALUES (?,?,?,'draft',?,?,?,?,?)""",
        (company_id, run_no, period, gross, deductions, net, user["id"], utcnow()))
    run_id = cur.lastrowid
    je_lines = [
        {"account_id": account_id_by_code(conn, company_id, ACC["salary_expense"]),
         "debit_cents": gross, "credit_cents": 0, "description": f"مسير رواتب {period}"},
        {"account_id": account_id_by_code(conn, company_id, ACC["deductions_payable"]),
         "debit_cents": 0, "credit_cents": deductions, "description": f"استقطاعات {period}"},
        {"account_id": account_id_by_code(conn, company_id, ACC["payroll_payable"]),
         "debit_cents": 0, "credit_cents": net, "description": f"صافي الرواتب {period}"},
    ]
    je_id = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=period + "-28",
        memo=f"مسير رواتب {period}", lines=je_lines, entry_type="payroll",
        source_type="payroll_run", source_id=run_id, idempotency_key=f"payroll:{run_id}")
    for e, g, d, n in lines:
        conn.execute(
            """INSERT INTO payroll_lines (run_id, employee_id, basic_cents, allowances_cents, deductions_cents, net_cents)
               VALUES (?,?,?,?,?,?)""",
            (run_id, e["id"], e["basic_salary_cents"], e["allowances_cents"], d, n))
    conn.execute("UPDATE payroll_runs SET status='posted', journal_entry_id=? WHERE id=?", (je_id, run_id))
    audit(conn, user, "payroll.post", "payroll_run", run_id,
          {"run_no": run_no, "net": net / 100}, company_id=company_id)
    return {"run_id": run_id, "run_no": run_no, "gross_cents": gross, "net_cents": net, "journal_entry_id": je_id}
