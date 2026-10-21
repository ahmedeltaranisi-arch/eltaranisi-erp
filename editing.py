"""Editing (PATCH) service — the "edit" half of every create.

Design rules, mirroring `deletion.py`:

1. **Company isolation** — the row must belong to `user["company_id"]`.
2. **Whitelist only** — a request can only touch the columns listed in the spec;
   anything else in the body is ignored, so no request can rewrite `company_id`,
   `journal_entry_id` or an opening balance by hand.
3. **Posted documents are immutable** — accounting records that have hit the
   ledger may not be silently rewritten (same rule as "no edits after posting").
   Draft documents are editable; posted ones must be reversed first.
4. **Audit carries both sides** — every edit stores `{before, after}` in
   `audit_logs`, so a reviewer can see exactly what changed and by whom.
"""
from __future__ import annotations

from accounting import ValidationError, ensure_period_open
from db import to_cents
from security import audit


def _row(conn, sql, params):
    return conn.execute(sql, params).fetchone()


def _snap(row) -> dict:
    return {k: row[k] for k in row.keys()} if row is not None else {}


def _money(v) -> int:
    return to_cents(v)


def _int(v) -> int:
    return int(v or 0)


def _bool(v) -> int:
    if isinstance(v, str):
        return 1 if v.strip().lower() in ("1", "true", "yes", "on") else 0
    return 1 if v else 0


def _txt(v):
    return (str(v).strip() or None) if v is not None else None


# --------------------------------------------------------------------------
# Master data specs: api field -> (column, converter)
# --------------------------------------------------------------------------
MASTER_SPECS = {
    "customers": {
        "entity": "customer",
        "table": "customers",
        "label_ar": "العميل", "label_en": "customer",
        "unique": ["code"],
        "required": ["code", "name_ar", "name_en"],
        "fields": {
            "code": ("code", _txt),
            "name_ar": ("name_ar", _txt),
            "name_en": ("name_en", _txt),
            "phone": ("phone", _txt),
            "email": ("email", _txt),
            "tax_number": ("tax_number", _txt),
            "address": ("address", _txt),
            "credit_limit": ("credit_limit_cents", _money),
            "payment_terms_days": ("payment_terms_days", _int),
            "is_active": ("is_active", _bool),
        },
    },
    "vendors": {
        "entity": "vendor",
        "table": "vendors",
        "label_ar": "المورد", "label_en": "vendor",
        "unique": ["code"],
        "required": ["code", "name_ar", "name_en"],
        "fields": {
            "code": ("code", _txt),
            "name_ar": ("name_ar", _txt),
            "name_en": ("name_en", _txt),
            "phone": ("phone", _txt),
            "email": ("email", _txt),
            "tax_number": ("tax_number", _txt),
            "address": ("address", _txt),
            "payment_terms_days": ("payment_terms_days", _int),
            "is_active": ("is_active", _bool),
        },
    },
    "products": {
        "entity": "product",
        "table": "products",
        "label_ar": "الصنف", "label_en": "item",
        "unique": ["sku"],
        "required": ["sku", "name_ar", "name_en"],
        "fields": {
            "sku": ("sku", _txt),
            "name_ar": ("name_ar", _txt),
            "name_en": ("name_en", _txt),
            "unit": ("unit", _txt),
            "category": ("category", _txt),
            "sale_price": ("sale_price_cents", _money),
            "purchase_price": ("purchase_price_cents", _money),
            "tax_rate_bp": ("tax_rate_bp", _int),
            "reorder_level": ("reorder_level_milli", lambda v: int(float(v or 0) * 1000)),
            "is_service": ("is_service", _bool),
            "is_active": ("is_active", _bool),
        },
    },
    "employees": {
        "entity": "employee",
        "table": "employees",
        "label_ar": "الموظف", "label_en": "employee",
        "unique": ["code"],
        "required": ["code", "name_ar", "name_en", "hire_date"],
        "fields": {
            "code": ("code", _txt),
            "name_ar": ("name_ar", _txt),
            "name_en": ("name_en", _txt),
            "department": ("department", _txt),
            "job_title": ("job_title", _txt),
            "hire_date": ("hire_date", _txt),
            "basic_salary": ("basic_salary_cents", _money),
            "allowances": ("allowances_cents", _money),
            "deductions": ("deductions_cents", _money),
            "insurance": ("insurance_cents", _money),
            "tax": ("tax_cents", _money),
            "bank_name": ("bank_name", _txt),
            "iban": ("iban", _txt),
            "is_active": ("is_active", _bool),
        },
    },
    "bank-accounts": {
        "entity": "bank_account",
        "table": "bank_accounts",
        "label_ar": "البنك/الصندوق", "label_en": "bank/cash account",
        "unique": ["code"],
        "required": ["code", "name_ar", "name_en"],
        "fields": {
            "code": ("code", _txt),
            "name_ar": ("name_ar", _txt),
            "name_en": ("name_en", _txt),
            "account_type": ("account_type", _txt),
            "gl_account_id": ("gl_account_id", _int),
            "iban": ("iban", _txt),
            "currency": ("currency", _txt),
            "is_active": ("is_active", _bool),
        },
    },
    "fixed-assets": {
        "entity": "fixed_asset",
        "table": "fixed_assets",
        "label_ar": "الأصل", "label_en": "asset",
        "unique": ["code"],
        "required": ["code", "name_ar", "name_en"],
        "fields": {
            "code": ("code", _txt),
            "name_ar": ("name_ar", _txt),
            "name_en": ("name_en", _txt),
            "category": ("category", _txt),
            "purchase_date": ("purchase_date", _txt),
            "start_date": ("start_date", _txt),
            "cost": ("cost_cents", _money),
            "salvage": ("salvage_cents", _money),
            "useful_life_months": ("useful_life_months", _int),
            "status": ("status", _txt),
        },
    },
}


def update_master(conn, user, company_id: int, kind: str, row_id: int, body: dict, ip=None) -> dict:
    """Update one master-data row from a whitelisted spec."""
    spec = MASTER_SPECS.get(kind)
    if spec is None:
        raise ValidationError(f"UNKNOWN_ENTITY: {kind}", status=400)
    table = spec["table"]
    current = _row(conn, f"SELECT * FROM {table} WHERE id=? AND company_id=?", (row_id, company_id))
    if current is None:
        raise ValidationError(
            f"NOT_FOUND: {spec['label_ar']} غير موجود / {spec['label_en']} not found", status=404)

    changes, values = {}, []
    for api_field, (column, conv) in spec["fields"].items():
        if api_field not in body or body[api_field] is None and api_field not in spec["required"]:
            continue
        if api_field not in body:
            continue
        try:
            new_value = conv(body[api_field])
        except (TypeError, ValueError):
            raise ValidationError(
                f"INVALID_VALUE: قيمة غير صحيحة للحقل {api_field} / invalid {api_field}",
                details={"field": api_field})
        if api_field in spec["required"] and (new_value is None or new_value == ""):
            raise ValidationError(f"MISSING_FIELD: {api_field} مطلوب / required",
                                  details={"field": api_field})
        changes[column] = new_value
        values.append(new_value)

    if not changes:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")

    # uniqueness inside the company
    for col in spec["unique"]:
        if col in changes and changes[col] != current[col]:
            clash = conn.execute(
                f"SELECT id FROM {table} WHERE company_id=? AND {col}=? AND id<>?",
                (company_id, changes[col], row_id)).fetchone()
            if clash:
                raise ValidationError(
                    f"DUPLICATE: {col} مستخدم بالفعل / value already exists",
                    details={"field": col, "value": changes[col]}, status=409)

    # fixed assets: money/life cannot be rewritten once depreciation has started
    if kind == "fixed-assets" and current["accumulated_cents"] and \
            any(c in changes for c in ("cost_cents", "useful_life_months", "salvage_cents", "start_date")):
        raise ValidationError(
            "HAS_DEPRECIATION: لا يمكن تعديل تكلفة/عمر أصل بدأ إهلاكه "
            "/ Asset already depreciated — cost and life are locked", status=409)

    sets = ", ".join(f"{col}=?" for col in changes)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", tuple(values) + (row_id,))

    after = _row(conn, f"SELECT * FROM {table} WHERE id=?", (row_id,))
    entity = spec.get("entity") or kind.rstrip("s")
    audit(conn, user, f"{entity}.update", entity, row_id,
          {"before": _snap(current), "after": _snap(after), "changed": list(changes)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": entity, "id": row_id, "changed": list(changes)}


# --------------------------------------------------------------------------
# Draft documents: sales invoice, purchase invoice, journal entry
# --------------------------------------------------------------------------
def _require_draft(row, label_ar, label_en):
    if row["status"] not in ("draft",):
        raise ValidationError(
            f"ALREADY_POSTED: لا يمكن تعديل {label_ar} بعد الترحيل — اعكسها أولًا "
            f"/ {label_en} is already posted; reverse it instead",
            details={"status": row["status"]}, status=409)


def update_sales_invoice(conn, user, company_id: int, invoice_id: int, body: dict, ip=None) -> dict:
    inv = _row(conn, "SELECT * FROM sales_invoices WHERE id=? AND company_id=?", (invoice_id, company_id))
    if inv is None:
        raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", status=404)
    _require_draft(inv, "فاتورة مبيعات مرحّلة", "sales invoice")
    ensure_period_open(conn, company_id, inv["invoice_date"])

    before = _snap(inv)
    fields, values = {}, []
    for api_field, column, conv in (("customer_id", "customer_id", _int),
                                    ("invoice_date", "invoice_date", _txt),
                                    ("due_date", "due_date", _txt),
                                    ("warehouse_id", "warehouse_id", _int),
                                    ("notes", "notes", _txt)):
        if api_field in body and body[api_field] is not None:
            fields[column] = conv(body[api_field])
            values.append(fields[column])
    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")
    if "invoice_date" in fields:
        ensure_period_open(conn, company_id, fields["invoice_date"])

    sets = ", ".join(f"{c}=?" for c in fields)
    conn.execute(f"UPDATE sales_invoices SET {sets} WHERE id=?", tuple(values) + (invoice_id,))
    after = _row(conn, "SELECT * FROM sales_invoices WHERE id=?", (invoice_id,))
    audit(conn, user, "sales_invoice.update", "sales_invoice", invoice_id,
          {"before": before, "after": _snap(after), "changed": list(fields)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": "sales_invoice", "id": invoice_id, "changed": list(fields)}


def update_purchase_invoice(conn, user, company_id: int, invoice_id: int, body: dict, ip=None) -> dict:
    inv = _row(conn, "SELECT * FROM purchase_invoices WHERE id=? AND company_id=?", (invoice_id, company_id))
    if inv is None:
        raise ValidationError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", status=404)
    _require_draft(inv, "فاتورة مشتريات مرحّلة", "purchase invoice")
    ensure_period_open(conn, company_id, inv["invoice_date"])

    before = _snap(inv)
    fields, values = {}, []
    for api_field, column, conv in (("vendor_id", "vendor_id", _int),
                                    ("invoice_date", "invoice_date", _txt),
                                    ("due_date", "due_date", _txt),
                                    ("warehouse_id", "warehouse_id", _int),
                                    ("supplier_ref", "supplier_ref", _txt),
                                    ("po_number", "po_number", _txt),
                                    ("grn_number", "grn_number", _txt),
                                    ("notes", "notes", _txt)):
        if api_field in body and body[api_field] is not None:
            fields[column] = conv(body[api_field])
            values.append(fields[column])
    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")

    sets = ", ".join(f"{c}=?" for c in fields)
    conn.execute(f"UPDATE purchase_invoices SET {sets} WHERE id=?", tuple(values) + (invoice_id,))
    after = _row(conn, "SELECT * FROM purchase_invoices WHERE id=?", (invoice_id,))
    audit(conn, user, "purchase_invoice.update", "purchase_invoice", invoice_id,
          {"before": before, "after": _snap(after), "changed": list(fields)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": "purchase_invoice", "id": invoice_id, "changed": list(fields)}


def update_journal_entry(conn, user, company_id: int, entry_id: int, body: dict, ip=None) -> dict:
    ent = _row(conn, "SELECT * FROM journal_entries WHERE id=? AND company_id=?", (entry_id, company_id))
    if ent is None:
        raise ValidationError("NOT_FOUND: القيد غير موجود / Entry not found", status=404)
    if ent["source_type"]:
        raise ValidationError(
            "SYSTEM_ENTRY: هذا القيد ناتج عن مستند — عدّل المستند نفسه "
            "/ Entry was generated by a document; edit the document instead", status=409)
    _require_draft(ent, "قيد مرحّل", "journal entry")
    ensure_period_open(conn, company_id, ent["entry_date"])

    before = _snap(ent)
    fields, values = {}, []
    if "entry_date" in body and body["entry_date"]:
        ensure_period_open(conn, company_id, body["entry_date"])
        fields["entry_date"] = _txt(body["entry_date"]); values.append(fields["entry_date"])
    if "memo" in body and body["memo"]:
        fields["memo"] = _txt(body["memo"]); values.append(fields["memo"])
    if "cost_center_id" in body:
        fields["cost_center_id"] = body["cost_center_id"] or None
        values.append(fields["cost_center_id"])
    if "project_id" in body:
        fields["project_id"] = body["project_id"] or None
        values.append(fields["project_id"])

    lines = body.get("lines")
    if lines:
        total_d = sum(to_cents(l.get("debit")) for l in lines)
        total_c = sum(to_cents(l.get("credit")) for l in lines)
        if total_d != total_c:
            raise ValidationError("UNBALANCED: القيد غير متوازن / Entry is not balanced",
                                  details={"debit": total_d, "credit": total_c})
        conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (entry_id,))
        for l in lines:
            conn.execute("""INSERT INTO journal_lines (entry_id, line_no, account_id, debit_cents,
                            credit_cents, description) VALUES (?,?,?,?,?,?)""",
                         (entry_id, int(l.get("line_no") or 1), int(l["account_id"]),
                          to_cents(l.get("debit")), to_cents(l.get("credit")), l.get("description")))
        fields["total_debit_cents"] = total_d
        fields["total_credit_cents"] = total_c
        values += [total_d, total_c]

    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")

    sets = ", ".join(f"{c}=?" for c in fields)
    conn.execute(f"UPDATE journal_entries SET {sets} WHERE id=?", tuple(values) + (entry_id,))
    after = _row(conn, "SELECT * FROM journal_entries WHERE id=?", (entry_id,))
    audit(conn, user, "journal_entry.update", "journal_entry", entry_id,
          {"before": before, "after": _snap(after), "changed": list(fields)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": "journal_entry", "id": entry_id, "changed": list(fields)}


def update_payment(conn, user, company_id: int, payment_id: int, body: dict, ip=None) -> dict:
    """Payments: only descriptive fields move once money is allocated."""
    pay = _row(conn, "SELECT * FROM payments WHERE id=? AND company_id=?", (payment_id, company_id))
    if pay is None:
        raise ValidationError("NOT_FOUND: السند غير موجود / Payment not found", status=404)
    ensure_period_open(conn, company_id, pay["payment_date"])

    before = _snap(pay)
    fields, values = {}, []
    if "reference" in body:
        fields["reference"] = _txt(body["reference"]); values.append(fields["reference"])
    if "notes" in body:
        fields["notes"] = _txt(body["notes"]); values.append(fields["notes"])
    if "payment_date" in body and body["payment_date"]:
        if pay["status"] == "posted":
            raise ValidationError(
                "ALREADY_POSTED: تاريخ سند مرحّل لا يُعدّل — اعكسه ثم أنشئ سندًا جديدًا "
                "/ Posted payment date is locked; reverse it instead", status=409)
        ensure_period_open(conn, company_id, body["payment_date"])
        fields["payment_date"] = _txt(body["payment_date"]); values.append(fields["payment_date"])
    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")

    sets = ", ".join(f"{c}=?" for c in fields)
    conn.execute(f"UPDATE payments SET {sets} WHERE id=?", tuple(values) + (payment_id,))
    after = _row(conn, "SELECT * FROM payments WHERE id=?", (payment_id,))
    audit(conn, user, "payment.update", "payment", payment_id,
          {"before": before, "after": _snap(after), "changed": list(fields)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": "payment", "id": payment_id, "changed": list(fields)}


def update_bank_transaction(conn, user, company_id: int, txn_id: int, body: dict, ip=None) -> dict:
    txn = _row(conn, "SELECT * FROM bank_transactions WHERE id=? AND company_id=?", (txn_id, company_id))
    if txn is None:
        raise ValidationError("NOT_FOUND: الحركة غير موجودة / Transaction not found", status=404)
    if txn["is_reconciled"]:
        raise ValidationError(
            "RECONCILED: حركة مطابَقة لا تُعدّل — ألغِ المطابقة أولًا "
            "/ Reconciled transaction is locked", status=409)

    before = _snap(txn)
    fields, values = {}, []
    for api_field, column, conv in (("txn_date", "txn_date", _txt),
                                    ("description", "description", _txt),
                                    ("statement_ref", "statement_ref", _txt),
                                    ("debit", "debit_cents", _money),
                                    ("credit", "credit_cents", _money),
                                    ("bank_account_id", "bank_account_id", _int)):
        if api_field in body and body[api_field] is not None:
            fields[column] = conv(body[api_field]); values.append(fields[column])
    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")

    sets = ", ".join(f"{c}=?" for c in fields)
    conn.execute(f"UPDATE bank_transactions SET {sets} WHERE id=?", tuple(values) + (txn_id,))
    after = _row(conn, "SELECT * FROM bank_transactions WHERE id=?", (txn_id,))
    audit(conn, user, "bank_transaction.update", "bank_transaction", txn_id,
          {"before": before, "after": _snap(after), "changed": list(fields)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": "bank_transaction", "id": txn_id, "changed": list(fields)}


def update_budget(conn, user, company_id: int, budget_id: int, body: dict, ip=None) -> dict:
    row = _row(conn, "SELECT * FROM budgets WHERE id=? AND company_id=?", (budget_id, company_id))
    if row is None:
        raise ValidationError("NOT_FOUND: بند الموازنة غير موجود / Budget line not found", status=404)
    before = _snap(row)
    fields, values = {}, []
    if "budget" in body and body["budget"] is not None:
        fields["budget_cents"] = _money(body["budget"]); values.append(fields["budget_cents"])
    if "period_month" in body and body["period_month"]:
        month = _int(body["period_month"])
        if not 1 <= month <= 12:
            raise ValidationError("INVALID_STATE: الشهر يجب أن يكون بين 1 و12 / month 1..12")
        fields["period_month"] = month; values.append(month)
    if "notes" in body:
        fields["notes"] = _txt(body["notes"]); values.append(fields["notes"])
    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد حقول للتعديل / Nothing to update")

    sets = ", ".join(f"{c}=?" for c in fields)
    conn.execute(f"UPDATE budgets SET {sets} WHERE id=?", tuple(values) + (budget_id,))
    after = _row(conn, "SELECT * FROM budgets WHERE id=?", (budget_id,))
    audit(conn, user, "budget.update", "budget", budget_id,
          {"before": before, "after": _snap(after), "changed": list(fields)},
          company_id=company_id, ip=ip)
    return {"ok": True, "updated": "budget", "id": budget_id, "changed": list(fields)}
