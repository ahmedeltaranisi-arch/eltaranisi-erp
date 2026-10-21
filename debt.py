"""Debt module (مديونية المقاولين والموردين) for a shipping & stevedoring company.

The company does not resell goods: it bills by **quantity** — طن، نقلة، حاوية،
ساعة، إيجار شهري — per contractor, and settles with صرف vouchers. Every account
therefore has:

* مستحقات (``debt_items``): البيان / العملية + الكمية × فئة الوحدة = الإجمالي
  المستحق, optionally tied to a vessel (اسم المركب), the client (اسم العميل),
  the loading period (فترة الشحن) and the operation type (شحن / تفريغ).
* سدادات (``debt_payments``): المبلغ + التاريخ + اذن صرف + من صرف/استلم.

``statement()`` reproduces the paper statement exactly: الإجمالي المستحق،
الدفعات المستلمة، صافي المستحق. ``summary()`` reproduces the consolidated
«حساب المديونية» page: one line per account grouped by type with subtotals.

Every posted item/payment owns a journal entry, so the sub-ledger always ties to
the GL (Dr cost of service / Cr trade payables, and Dr trade payables / Cr cash
or bank). Editing re-posts; deleting removes the entry and its lines.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import accounting
from accounting import ValidationError
from db import to_cents as _to_cents
from db import to_milli as _to_milli
from security import audit, utcnow


KINDS = {
    "contractor": ("مقاول", "Contractor"),
    "labor": ("عمالة", "Labour"),
    "equipment": ("معدات وإيجار", "Equipment & hire"),
    "transport": ("نقل وحاويات", "Transport & containers"),
    "fuel": ("وقود وسولار", "Fuel"),
    "workshop": ("ورش وصيانة", "Workshop & maintenance"),
    "supplies": ("مشتريات متنوعة", "Supplies"),
    "other": ("أخرى", "Other"),
}

UNITS = {
    "ton": ("طن", "Ton"),
    "trip": ("نقلة", "Trip"),
    "container": ("حاوية", "Container"),
    "hour": ("ساعة", "Hour"),
    "month": ("شهر (إيجار شهري)", "Month (hire)"),
    "item": ("بند / عدد", "Item / count"),
}

METHODS = {"cash": "نقدية", "bank": "تحويل بنكي", "cheque": "شيك", "transfer": "تحويل"}

ACC_COST_OF_SERVICE = "5100"   # تكلفة المبيعات / cost of service
ACC_PAYABLES = "2120"          # مستحقات المقاولين — debt sub-ledger control account
ACC_TRADE_PAYABLES = "2110"    # الموردون (trade goods, fed by purchase invoices)
ACC_CASH = "1111"              # صندوق دمياط الرئيسي


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _account(conn, company_id: int, account_id: int):
    row = conn.execute("SELECT * FROM debt_accounts WHERE id=? AND company_id=?",
                       (account_id, company_id)).fetchone()
    if row is None:
        raise ValidationError("NOT_FOUND: حساب المديونية غير موجود / Debt account not found")
    return row


def _amount(qty_milli: int, rate_cents: int) -> int:
    if not qty_milli or not rate_cents:
        return 0
    return int((Decimal(qty_milli) * Decimal(rate_cents) / Decimal(1000))
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _gl(conn, company_id: int, code: str) -> int:
    return accounting.account_id_by_code(conn, company_id, code)


def payable_account(conn, company_id: int) -> int:
    """Control account for contractor dues (2120).

    Contractor/stevedoring dues are a separate sub-ledger from trade payables:
    keeping them on 2110 would make the vendor sub-ledger stop reconciling with
    its own control account. The account is created on demand so databases
    seeded before this round keep working.
    """
    row = conn.execute("SELECT id FROM accounts WHERE company_id=? AND code=?",
                       (company_id, ACC_PAYABLES)).fetchone()
    if row:
        return row["id"]
    parent = conn.execute("SELECT id FROM accounts WHERE company_id=? AND code='21'",
                          (company_id,)).fetchone()
    cur = conn.execute(
        """INSERT INTO accounts (company_id, code, name_ar, name_en, type, parent_id, level,
              is_postable, is_active, currency, closing_type)
           VALUES (?,?,?,?,'liability',?,4,1,1,'EGP','balance')""",
        (company_id, ACC_PAYABLES, "مستحقات المقاولين والموردين المتنوعين",
         "Contractor & Sundry Payables", parent["id"] if parent else None))
    return cur.lastrowid


def _detach_entry(conn, table: str, doc_id: int, entry_id) -> None:
    """Clear the document → journal FK *before* the voucher is removed.

    ``debt_items.journal_entry_id`` / ``debt_payments.journal_entry_id`` are real
    foreign keys, so the row must stop pointing at the entry first; deleting in
    the other order trips the FK constraint.
    """
    if not entry_id:
        return
    conn.execute(f"UPDATE {table} SET journal_entry_id=NULL WHERE id=?", (doc_id,))
    _drop_entry(conn, entry_id)


def _drop_entry(conn, entry_id) -> None:
    """Remove a generated journal entry so the document can be re-posted.

    Debt documents are sub-ledger lines: when one is edited or deleted its
    voucher is replaced, exactly like a corrected payment voucher. The audit log
    keeps the before/after snapshot, so nothing disappears silently.
    """
    if not entry_id:
        return
    row = conn.execute("SELECT status FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        return
    if row["status"] == "reversed":
        return
    conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (entry_id,))
    conn.execute("DELETE FROM journal_entries WHERE id=?", (entry_id,))


def _outstanding_before(conn, company_id: int, account_id: int, exclude_payment_id=None,
                        exclude_item_id=None) -> int:
    """Due (incl. opening balance) minus settlements already posted.

    Used to decide whether a new settlement covers real dues or is an advance
    (دفعة مقدمة) taken before the invoice/trip statement exists. The document
    being posted is still a draft at this point, so it never counts as paid.
    """
    acc = conn.execute("SELECT opening_balance_cents FROM debt_accounts WHERE id=?",
                       (account_id,)).fetchone()
    opening = int(acc["opening_balance_cents"] or 0) if acc else 0
    due = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS s FROM debt_items "
        "WHERE company_id=? AND account_id=? AND status='posted' AND id<>?",
        (company_id, account_id, exclude_item_id or -1)).fetchone()["s"]
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS s FROM debt_payments "
        "WHERE company_id=? AND account_id=? AND status='posted' AND id<>?",
        (company_id, account_id, exclude_payment_id or -1)).fetchone()["s"]
    return opening + int(due) - int(paid)


def _totals(conn, company_id: int, account_id: int, d1: str | None = None, d2: str | None = None) -> dict:
    where = ["company_id=?", "account_id=?", "status='posted'"]
    params: list = [company_id, account_id]
    if d1:
        where.append("doc_date>=?")
        params.append(d1)
    if d2:
        where.append("doc_date<=?")
        params.append(d2)
    due = conn.execute(f"SELECT COALESCE(SUM(amount_cents),0) AS s FROM debt_items WHERE {' AND '.join(where)}",
                       params).fetchone()["s"]
    w2 = ["company_id=?", "account_id=?", "status='posted'"]
    p2: list = [company_id, account_id]
    if d1:
        w2.append("pay_date>=?")
        p2.append(d1)
    if d2:
        w2.append("pay_date<=?")
        p2.append(d2)
    paid = conn.execute(f"SELECT COALESCE(SUM(amount_cents),0) AS s FROM debt_payments WHERE {' AND '.join(w2)}",
                        p2).fetchone()["s"]
    return {"due_cents": int(due), "paid_cents": int(paid)}


# ---------------------------------------------------------------------------
# accounts (حسابات المديونية)
# ---------------------------------------------------------------------------
def list_accounts(conn, company_id: int) -> list:
    rows = [dict(r) for r in conn.execute(
        """SELECT a.*,
                  COALESCE((SELECT SUM(i.amount_cents) FROM debt_items i
                            WHERE i.account_id=a.id AND i.status='posted'),0) AS due_cents,
                  COALESCE((SELECT SUM(p.amount_cents) FROM debt_payments p
                            WHERE p.account_id=a.id AND p.status='posted'),0) AS paid_cents,
                  (SELECT COUNT(*) FROM debt_items i WHERE i.account_id=a.id) AS items_count,
                  (SELECT COUNT(*) FROM debt_payments p WHERE p.account_id=a.id) AS payments_count
             FROM debt_accounts a
            WHERE a.company_id=? ORDER BY a.code""", (company_id,)).fetchall()]
    for r in rows:
        r["paid_cents"] = int(r["paid_cents"])
        r["due_cents"] = int(r["due_cents"])
        r["opening_balance_cents"] = int(r["opening_balance_cents"] or 0)
        r["net_cents"] = r["opening_balance_cents"] + r["due_cents"] - r["paid_cents"]
        r["advance_cents"] = max(0, -r["net_cents"])          # دفعات مقدمة
        r["open_due_cents"] = max(0, r["net_cents"])
        r["kind_label_ar"], r["kind_label_en"] = KINDS.get(r["kind"], KINDS["other"])
        r["unit_label_ar"], r["unit_label_en"] = UNITS.get(r["billing_unit"], UNITS["item"])
    return rows


def create_account(conn, user, company_id: int, body: dict) -> int:
    code = (body.get("code") or "").strip()
    name_ar = (body.get("name_ar") or "").strip()
    if not name_ar:
        raise ValidationError("MISSING_FIELD: اسم الحساب مطلوب / Account name is required")
    if conn.execute("SELECT 1 FROM debt_accounts WHERE company_id=? AND code=?",
                    (company_id, code)).fetchone() and code:
        raise ValidationError("DUPLICATE: الكود مستخدم / Code already exists")
    if not code:
        n = conn.execute("SELECT COUNT(*) AS c FROM debt_accounts WHERE company_id=?",
                         (company_id,)).fetchone()["c"] + 1
        code = f"DB-{n:04d}"
        while conn.execute("SELECT 1 FROM debt_accounts WHERE company_id=? AND code=?",
                           (company_id, code)).fetchone():
            n += 1
            code = f"DB-{n:04d}"
    kind = body.get("kind") if body.get("kind") in KINDS else "contractor"
    unit = body.get("billing_unit") if body.get("billing_unit") in UNITS else "item"
    expense_acc = body.get("expense_account_id") or _gl(conn, company_id, body.get("expense_code") or ACC_COST_OF_SERVICE)
    cur = conn.execute(
        """INSERT INTO debt_accounts (company_id, code, name_ar, name_en, kind, billing_unit, vendor_id,
              expense_account_id, opening_balance_cents, is_active, notes, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, code, name_ar, (body.get("name_en") or name_ar).strip(), kind, unit,
         body.get("vendor_id") or None, expense_acc,
         _to_cents(body.get("opening_balance") or 0), 1 if body.get("is_active", True) else 0,
         body.get("notes"), user["id"], utcnow()))
    audit(conn, user, "debt_account.create", "debt_account", cur.lastrowid,
          {"code": code, "name_ar": name_ar, "kind": kind}, company_id=company_id)
    return cur.lastrowid


def update_account(conn, user, company_id: int, account_id: int, body: dict) -> dict:
    row = _account(conn, company_id, account_id)
    before = {k: row[k] for k in ("code", "name_ar", "name_en", "kind", "billing_unit",
                                  "opening_balance_cents", "is_active", "notes")}
    fields, params, changed = [], [], []
    mapping = {
        "code": ("code", lambda v: (v or "").strip()),
        "name_ar": ("name_ar", lambda v: (v or "").strip()),
        "name_en": ("name_en", lambda v: (v or "").strip()),
        "kind": ("kind", lambda v: v if v in KINDS else "contractor"),
        "billing_unit": ("billing_unit", lambda v: v if v in UNITS else "item"),
        "notes": ("notes", lambda v: v),
        "opening_balance": ("opening_balance_cents", _to_cents),
        "is_active": ("is_active", lambda v: 1 if v else 0),
        "expense_account_id": ("expense_account_id", lambda v: v or None),
    }
    for key, (col, cast) in mapping.items():
        if key in body:
            val = cast(body[key])
            if key == "name_ar" and not val:
                raise ValidationError("MISSING_FIELD: اسم الحساب مطلوب / Account name is required")
            fields.append(f"{col}=?")
            params.append(val)
            changed.append(key)
    if not fields:
        raise ValidationError("NOTHING_TO_UPDATE: لا توجد بيانات للتعديل / Nothing to update")
    if "code" in body:
        dup = conn.execute("SELECT 1 FROM debt_accounts WHERE company_id=? AND code=? AND id<>?",
                           (company_id, (body.get("code") or "").strip(), account_id)).fetchone()
        if dup:
            raise ValidationError("DUPLICATE: الكود مستخدم / Code already exists")
    params.append(account_id)
    conn.execute(f"UPDATE debt_accounts SET {', '.join(fields)} WHERE id=?", params)
    after = _account(conn, company_id, account_id)
    audit(conn, user, "debt_account.update", "debt_account", account_id,
          {"before": before, "after": {k: after[k] for k in before}, "changed": changed},
          company_id=company_id)
    return {"ok": True, "updated": "debt_account", "id": account_id, "changed": changed}


def delete_account(conn, user, company_id: int, account_id: int) -> dict:
    row = _account(conn, company_id, account_id)
    items = conn.execute("SELECT COUNT(*) AS c FROM debt_items WHERE account_id=?", (account_id,)).fetchone()["c"]
    pays = conn.execute("SELECT COUNT(*) AS c FROM debt_payments WHERE account_id=?", (account_id,)).fetchone()["c"]
    if items or pays:
        raise ValidationError(
            f"HAS_CHILDREN: احذف المستحقات ({items}) والسدادات ({pays}) أولًا / "
            f"Delete the {items} items and {pays} payments first",
            {"items": items, "payments": pays}, status=409)
    conn.execute("DELETE FROM debt_accounts WHERE id=?", (account_id,))
    audit(conn, user, "debt_account.delete", "debt_account", account_id,
          {"code": row["code"], "name_ar": row["name_ar"]}, company_id=company_id)
    return {"ok": True, "deleted": "debt_account", "id": account_id, "code": row["code"]}


# ---------------------------------------------------------------------------
# items (المستحقات)
# ---------------------------------------------------------------------------
def list_items(conn, company_id: int, account_id: int | None = None,
               d1: str | None = None, d2: str | None = None) -> list:
    where = ["i.company_id=?"]
    params: list = [company_id]
    if account_id:
        where.append("i.account_id=?")
        params.append(account_id)
    if d1:
        where.append("i.doc_date>=?")
        params.append(d1)
    if d2:
        where.append("i.doc_date<=?")
        params.append(d2)
    rows = [dict(r) for r in conn.execute(
        f"""SELECT i.*, a.code AS account_code, a.name_ar AS account_name_ar, a.name_en AS account_name_en
              FROM debt_items i JOIN debt_accounts a ON a.id=i.account_id
             WHERE {' AND '.join(where)} ORDER BY i.doc_date DESC, i.id DESC""", params).fetchall()]
    for r in rows:
        r["unit_label_ar"], r["unit_label_en"] = UNITS.get(r["unit"], UNITS["item"])
    return rows


def _item_payload(conn, company_id: int, body: dict, existing=None) -> dict:
    qty = body.get("qty")
    rate = body.get("unit_rate")
    qty_milli = _to_milli(qty) if qty not in (None, "") else (existing["qty_milli"] if existing else 0)
    rate_cents = _to_cents(rate) if rate not in (None, "") else (existing["unit_rate_cents"] if existing else 0)
    amount = body.get("amount")
    if amount not in (None, ""):
        amount_cents = _to_cents(amount)
    else:
        amount_cents = _amount(qty_milli, rate_cents)
        if not amount_cents and existing is not None:
            amount_cents = 0
    if amount_cents <= 0:
        raise ValidationError(
            "INVALID_AMOUNT: أدخل الكمية وفئة الوحدة أو مبلغ المستحق / Provide quantity × unit rate or an amount")
    return {
        "doc_date": body.get("doc_date") or (existing["doc_date"] if existing else date.today().isoformat()),
        "description": (body.get("description") or (existing["description"] if existing else "") or "").strip(),
        "operation": body.get("operation") if body.get("operation") is not None else (existing["operation"] if existing else None),
        "qty_milli": qty_milli, "unit": body.get("unit") or (existing["unit"] if existing else "item"),
        "unit_rate_cents": rate_cents, "amount_cents": amount_cents,
        "vessel": body.get("vessel") if body.get("vessel") is not None else (existing["vessel"] if existing else None),
        "client_name": body.get("client_name") if body.get("client_name") is not None else (existing["client_name"] if existing else None),
        "cargo": body.get("cargo") if body.get("cargo") is not None else (existing["cargo"] if existing else None),
        "period_from": (body.get("period_from") or None) if body.get("period_from") is not None else (existing["period_from"] if existing else None),
        "period_to": (body.get("period_to") or None) if body.get("period_to") is not None else (existing["period_to"] if existing else None),
        "report_no": body.get("report_no") if body.get("report_no") is not None else (existing["report_no"] if existing else None),
        "notes": body.get("notes") if body.get("notes") is not None else (existing["notes"] if existing else None),
    }


def _post_item(conn, user, company_id: int, item_id: int) -> int:
    item = conn.execute("SELECT * FROM debt_items WHERE id=? AND company_id=?",
                        (item_id, company_id)).fetchone()
    acc = _account(conn, company_id, item["account_id"])
    debit = acc["expense_account_id"] or _gl(conn, company_id, ACC_COST_OF_SERVICE)
    credit = payable_account(conn, company_id)
    label = f"{acc['name_ar']} — {item['description']}"
    je = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=item["doc_date"],
        memo=f"مستحق مديونية {item['item_no']} — {acc['name_ar']}",
        lines=[
            {"account_id": debit, "debit_cents": item["amount_cents"], "credit_cents": 0,
             "description": label, "vendor_id": acc["vendor_id"]},
            {"account_id": credit, "debit_cents": 0, "credit_cents": item["amount_cents"],
             "description": label, "vendor_id": acc["vendor_id"]},
        ],
        entry_type="purchase", source_type="debt_item", source_id=item_id,
        idempotency_key=f"debt_item:{item_id}",
    )
    conn.execute("UPDATE debt_items SET status='posted', journal_entry_id=? WHERE id=?", (je, item_id))
    return je


def _post_payment(conn, user, company_id: int, payment_id: int) -> int:
    pay = conn.execute("SELECT * FROM debt_payments WHERE id=? AND company_id=?",
                       (payment_id, company_id)).fetchone()
    acc = _account(conn, company_id, pay["account_id"])
    debit = payable_account(conn, company_id)
    credit = pay["gl_account_id"] or _gl(conn, company_id, ACC_CASH)
    # A settlement larger than what the account owes is an advance (دفعة مقدمة)
    # taken before the invoice / trip statement is issued: the excess sits as a
    # debit on the contractor control account and is consumed automatically by
    # the next due line booked to the same account.
    outstanding = _outstanding_before(conn, company_id, acc["id"], exclude_payment_id=payment_id)
    advance = max(0, int(pay["amount_cents"]) - max(0, outstanding))
    conn.execute("UPDATE debt_payments SET advance_cents=? WHERE id=?", (advance, payment_id))
    kind = "دفعة مقدمة" if advance and advance == int(pay["amount_cents"]) else (
        "سداد جزئي بدفعة مقدمة" if advance else "سداد")
    label = f"{kind} {acc['name_ar']}" + (f" — اذن صرف {pay['voucher_no']}" if pay["voucher_no"] else "")
    if pay["description"]:
        label += f" — {pay['description']}"
    je = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=pay["pay_date"],
        memo=f"سداد مديونية {pay['payment_no']} — {acc['name_ar']}",
        lines=[
            {"account_id": debit, "debit_cents": pay["amount_cents"], "credit_cents": 0,
             "description": label, "vendor_id": acc["vendor_id"]},
            {"account_id": credit, "debit_cents": 0, "credit_cents": pay["amount_cents"],
             "description": label},
        ],
        entry_type="payment", source_type="debt_payment", source_id=payment_id,
        idempotency_key=f"debt_payment:{payment_id}",
    )
    conn.execute("UPDATE debt_payments SET status='posted', journal_entry_id=? WHERE id=?", (je, payment_id))
    return je


def create_item(conn, user, company_id: int, body: dict) -> int:
    acc = _account(conn, company_id, int(body.get("account_id") or 0))
    data = _item_payload(conn, company_id, body)
    if not data["description"]:
        raise ValidationError("MISSING_FIELD: البيان مطلوب / Description is required")
    if not data["unit"] and acc["billing_unit"]:
        data["unit"] = acc["billing_unit"]
    no = accounting.next_number(conn, company_id, "debt_item", data["doc_date"])
    cur = conn.execute(
        """INSERT INTO debt_items (company_id, account_id, item_no, doc_date, description, operation,
              qty_milli, unit, unit_rate_cents, amount_cents, vessel, client_name, cargo, period_from,
              period_to, report_no, status, notes, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?,?)""",
        (company_id, acc["id"], no, data["doc_date"], data["description"], data["operation"],
         data["qty_milli"], data["unit"], data["unit_rate_cents"], data["amount_cents"],
         data["vessel"], data["client_name"], data["cargo"], data["period_from"], data["period_to"],
         data["report_no"], data["notes"], user["id"], utcnow()))
    item_id = cur.lastrowid
    if body.get("post", True):
        _post_item(conn, user, company_id, item_id)
    audit(conn, user, "debt_item.create", "debt_item", item_id,
          {"item_no": no, "account": acc["code"], "amount": data["amount_cents"] / 100}, company_id=company_id)
    return item_id


def update_item(conn, user, company_id: int, item_id: int, body: dict) -> dict:
    item = conn.execute("SELECT * FROM debt_items WHERE id=? AND company_id=?",
                        (item_id, company_id)).fetchone()
    if item is None:
        raise ValidationError("NOT_FOUND: المستحق غير موجود / Debt item not found")
    data = _item_payload(conn, company_id, body, existing=item)
    was_posted = item["status"] == "posted"
    if was_posted:
        _detach_entry(conn, "debt_items", item_id, item["journal_entry_id"])
    conn.execute(
        """UPDATE debt_items SET doc_date=?, description=?, operation=?, qty_milli=?, unit=?,
              unit_rate_cents=?, amount_cents=?, vessel=?, client_name=?, cargo=?, period_from=?,
              period_to=?, report_no=?, notes=?, status='draft', journal_entry_id=NULL, updated_at=?
            WHERE id=?""",
        (data["doc_date"], data["description"], data["operation"], data["qty_milli"], data["unit"],
         data["unit_rate_cents"], data["amount_cents"], data["vessel"], data["client_name"],
         data["cargo"], data["period_from"], data["period_to"], data["report_no"], data["notes"],
         utcnow(), item_id))
    post = body.get("post", was_posted)
    if post:
        _post_item(conn, user, company_id, item_id)
    audit(conn, user, "debt_item.update", "debt_item", item_id,
          {"item_no": item["item_no"], "amount": data["amount_cents"] / 100}, company_id=company_id)
    return {"ok": True, "updated": "debt_item", "id": item_id, "reposted": bool(post)}


def delete_item(conn, user, company_id: int, item_id: int) -> dict:
    item = conn.execute("SELECT * FROM debt_items WHERE id=? AND company_id=?",
                        (item_id, company_id)).fetchone()
    if item is None:
        raise ValidationError("NOT_FOUND: المستحق غير موجود / Debt item not found")
    _detach_entry(conn, "debt_items", item_id, item["journal_entry_id"])
    conn.execute("DELETE FROM debt_items WHERE id=?", (item_id,))
    audit(conn, user, "debt_item.delete", "debt_item", item_id,
          {"item_no": item["item_no"], "description": item["description"],
           "amount": item["amount_cents"] / 100}, company_id=company_id)
    return {"ok": True, "deleted": "debt_item", "id": item_id, "number": item["item_no"]}


# ---------------------------------------------------------------------------
# payments (السدادات — اذن صرف)
# ---------------------------------------------------------------------------
def list_payments(conn, company_id: int, account_id: int | None = None,
                  d1: str | None = None, d2: str | None = None) -> list:
    where = ["p.company_id=?"]
    params: list = [company_id]
    if account_id:
        where.append("p.account_id=?")
        params.append(account_id)
    if d1:
        where.append("p.pay_date>=?")
        params.append(d1)
    if d2:
        where.append("p.pay_date<=?")
        params.append(d2)
    rows = [dict(r) for r in conn.execute(
        f"""SELECT p.*, a.code AS account_code, a.name_ar AS account_name_ar
              FROM debt_payments p JOIN debt_accounts a ON a.id=p.account_id
             WHERE {' AND '.join(where)} ORDER BY p.pay_date DESC, p.id DESC""", params).fetchall()]
    for r in rows:
        r["method_label_ar"] = METHODS.get(r["method"], r["method"])
    return rows


def _method(value) -> str:
    v = (value or "cash").strip().lower()
    aliases = {"نقدية": "cash", "نقدي": "cash", "كاش": "cash", "بنك": "bank", "تحويل": "transfer",
               "تحويل بنكي": "bank", "شيك": "cheque", "cheque": "cheque", "check": "cheque"}
    v = aliases.get(v, v)
    if v not in METHODS:
        raise ValidationError("INVALID_METHOD: طريقة الدفع غير صحيحة / Invalid payment method")
    return v


def create_payment(conn, user, company_id: int, body: dict) -> int:
    acc = _account(conn, company_id, int(body.get("account_id") or 0))
    amount = _to_cents(body.get("amount") or 0)
    if amount <= 0:
        raise ValidationError("INVALID_AMOUNT: المبلغ يجب أن يكون موجبًا / Amount must be positive")
    method = _method(body.get("method"))
    gl = body.get("gl_account_id") or (_gl(conn, company_id, "1121") if method in ("bank", "transfer", "cheque")
                                       else _gl(conn, company_id, ACC_CASH))
    pay_date = body.get("pay_date") or date.today().isoformat()
    no = accounting.next_number(conn, company_id, "debt_payment", pay_date)
    cur = conn.execute(
        """INSERT INTO debt_payments (company_id, account_id, payment_no, voucher_no, pay_date,
              amount_cents, method, gl_account_id, payee, description, status, notes, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?,?)""",
        (company_id, acc["id"], no, (body.get("voucher_no") or "").strip() or None, pay_date, amount,
         method, gl, body.get("payee"), (body.get("description") or "").strip() or None,
         body.get("notes"), user["id"], utcnow()))
    pay_id = cur.lastrowid
    if body.get("post", True):
        _post_payment(conn, user, company_id, pay_id)
    audit(conn, user, "debt_payment.create", "debt_payment", pay_id,
          {"payment_no": no, "voucher_no": body.get("voucher_no"), "account": acc["code"],
           "amount": amount / 100}, company_id=company_id)
    return pay_id


def update_payment(conn, user, company_id: int, payment_id: int, body: dict) -> dict:
    pay = conn.execute("SELECT * FROM debt_payments WHERE id=? AND company_id=?",
                       (payment_id, company_id)).fetchone()
    if pay is None:
        raise ValidationError("NOT_FOUND: السداد غير موجود / Payment not found")
    amount = _to_cents(body.get("amount")) if body.get("amount") not in (None, "") else pay["amount_cents"]
    if amount <= 0:
        raise ValidationError("INVALID_AMOUNT: المبلغ يجب أن يكون موجبًا / Amount must be positive")
    method = _method(body.get("method") or pay["method"])
    gl = body.get("gl_account_id") or pay["gl_account_id"] or (
        _gl(conn, company_id, "1121") if method in ("bank", "transfer", "cheque")
        else _gl(conn, company_id, ACC_CASH))
    pay_date = body.get("pay_date") or pay["pay_date"]
    voucher = body.get("voucher_no") if body.get("voucher_no") is not None else pay["voucher_no"]
    was_posted = pay["status"] == "posted"
    if was_posted:
        _detach_entry(conn, "debt_payments", payment_id, pay["journal_entry_id"])
    description = body.get("description") if body.get("description") is not None else pay["description"]
    conn.execute(
        """UPDATE debt_payments SET pay_date=?, amount_cents=?, method=?, gl_account_id=?, voucher_no=?,
              payee=?, description=?, notes=?, status='draft', journal_entry_id=NULL, advance_cents=0,
              updated_at=? WHERE id=?""",
        (pay_date, amount, method, gl, (voucher or "").strip() or None,
         body.get("payee") if body.get("payee") is not None else pay["payee"],
         (description or "").strip() or None,
         body.get("notes") if body.get("notes") is not None else pay["notes"], utcnow(), payment_id))
    post = body.get("post", was_posted)
    if post:
        _post_payment(conn, user, company_id, payment_id)
    audit(conn, user, "debt_payment.update", "debt_payment", payment_id,
          {"payment_no": pay["payment_no"], "amount": amount / 100}, company_id=company_id)
    return {"ok": True, "updated": "debt_payment", "id": payment_id, "reposted": bool(post)}


def delete_payment(conn, user, company_id: int, payment_id: int) -> dict:
    pay = conn.execute("SELECT * FROM debt_payments WHERE id=? AND company_id=?",
                       (payment_id, company_id)).fetchone()
    if pay is None:
        raise ValidationError("NOT_FOUND: السداد غير موجود / Payment not found")
    _detach_entry(conn, "debt_payments", payment_id, pay["journal_entry_id"])
    conn.execute("DELETE FROM debt_payments WHERE id=?", (payment_id,))
    audit(conn, user, "debt_payment.delete", "debt_payment", payment_id,
          {"payment_no": pay["payment_no"], "amount": pay["amount_cents"] / 100}, company_id=company_id)
    return {"ok": True, "deleted": "debt_payment", "id": payment_id, "number": pay["payment_no"]}


# ---------------------------------------------------------------------------
# statement & consolidated summary
# ---------------------------------------------------------------------------
def statement(conn, company_id: int, account_id: int, d1: str | None = None, d2: str | None = None) -> dict:
    acc = _account(conn, company_id, account_id)
    items = list_items(conn, company_id, account_id, d1, d2)
    payments = list_payments(conn, company_id, account_id, d1, d2)
    t = _totals(conn, company_id, account_id, d1, d2)
    opening = int(acc["opening_balance_cents"] or 0)
    gross_due = opening + t["due_cents"]
    advance = max(0, t["paid_cents"] - gross_due)         # paid with no due to cover yet
    open_due = max(0, gross_due - t["paid_cents"])        # still owed to the contractor
    return {
        "account": dict(acc) | {
            "kind_label_ar": KINDS.get(acc["kind"], KINDS["other"])[0],
            "kind_label_en": KINDS.get(acc["kind"], KINDS["other"])[1],
            "unit_label_ar": UNITS.get(acc["billing_unit"], UNITS["item"])[0],
            "unit_label_en": UNITS.get(acc["billing_unit"], UNITS["item"])[1],
        },
        "items": items,
        "payments": payments,
        "totals": {
            "opening_cents": opening,
            "due_cents": t["due_cents"],
            "paid_cents": t["paid_cents"],
            "net_cents": gross_due - t["paid_cents"],
            "advance_cents": advance,      # دفعات مقدمة لم تُقابلها فاتورة بعد
            "open_due_cents": open_due,    # ما زال مستحقًا للمقاول
        },
        "range": {"from": d1, "to": d2},
    }


def summary(conn, company_id: int, d1: str | None = None, d2: str | None = None) -> dict:
    accounts = list_accounts(conn, company_id)
    if d1 or d2:
        for a in accounts:
            t = _totals(conn, company_id, a["id"], d1, d2)
            a["due_cents"], a["paid_cents"] = t["due_cents"], t["paid_cents"]
            a["net_cents"] = a["opening_balance_cents"] + t["due_cents"] - t["paid_cents"]
            a["advance_cents"] = max(0, -a["net_cents"])
            a["open_due_cents"] = max(0, a["net_cents"])
    groups = []
    for code, (label_ar, label_en) in KINDS.items():
        rows = [a for a in accounts if a["kind"] == code]
        if not rows:
            continue
        groups.append({
            "kind": code, "label_ar": label_ar, "label_en": label_en, "accounts": rows,
            "due_cents": sum(r["due_cents"] for r in rows),
            "paid_cents": sum(r["paid_cents"] for r in rows),
            "net_cents": sum(r["net_cents"] for r in rows),
            "advance_cents": sum(r["advance_cents"] for r in rows),
        })
    return {
        "groups": groups,
        "accounts": accounts,
        "totals": {
            "opening_cents": sum(a["opening_balance_cents"] for a in accounts),
            "due_cents": sum(a["due_cents"] for a in accounts),
            "paid_cents": sum(a["paid_cents"] for a in accounts),
            "net_cents": sum(a["net_cents"] for a in accounts),
            "advance_cents": sum(a["advance_cents"] for a in accounts),
            "open_due_cents": sum(a["open_due_cents"] for a in accounts),
            "accounts": len(accounts),
        },
        "range": {"from": d1, "to": d2},
    }


def invoices_statement(conn, company_id: int, d1: str | None = None, d2: str | None = None,
                       account_id: int | None = None) -> dict:
    """بيان الفواتير — every due line (invoice / trip statement) in one place.

    This is the paper «بيان» the accountant hands over: البيان، الحساب، التاريخ،
    الكمية، فئة الوحدة، الإجمالي — across all contractors, with filters and the
    coverage state (مسدَّد / غير مسدَّد) computed per account.
    """
    items = list_items(conn, company_id, account_id, d1, d2)
    accounts = {a["id"]: a for a in list_accounts(conn, company_id)}
    rows = []
    for it in items:
        acc = accounts.get(it["account_id"], {})
        rows.append({
            **it,
            "account_name": acc.get("name_ar"),
            "account_kind_ar": acc.get("kind_label_ar"),
            "unit_label_ar": it.get("unit_label_ar"),
            "paid_state": "posted" if it["status"] == "posted" else "draft",
        })
    totals = {
        "rows": len(rows),
        "amount_cents": sum(r["amount_cents"] for r in rows if r["status"] == "posted"),
        "draft_cents": sum(r["amount_cents"] for r in rows if r["status"] != "posted"),
    }
    return {"items": rows, "totals": totals,
            "accounts": [{"id": a["id"], "code": a["code"], "name_ar": a["name_ar"],
                          "advance_cents": a.get("advance_cents", 0)} for a in accounts.values()],
            "range": {"from": d1, "to": d2}}
