"""HTTP API + static file server for ELTARANISI ERP (Python stdlib only).

Run:  python3 server.py  →  http://0.0.0.0:8000
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import re
import traceback
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import accounting
import deletion
import documents
import editing
import reports
from db import BASE_DIR, connect, from_cents, money_str, read, to_cents, to_milli, transaction
from security import (AuthError, PermissionError_, audit, has, issue_token, load_user, sync_system_roles,
                      new_session_token, public_user, require, role_can, token_hash, utcnow,
                      verify_password, verify_token, notify)

# Serverless deployments keep the SQLite file in /tmp: each instance has its own
# copy, so a session row created by one instance is invisible to the next. When
# this flag is on, a correctly signed bearer token is accepted even if the row is
# missing (logout/revocation still wins wherever the row exists).
EPHEMERAL_AUTH = os.environ.get("ERP_EPHEMERAL_AUTH", "").strip().lower() in ("1", "true", "yes", "on")

STATIC_DIR = BASE_DIR / "static"
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = 8 * 1024 * 1024


# ==========================================================================
# helpers
# ==========================================================================
def j(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


class ApiError(Exception):
    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details or {}


def current_company(user):
    return user["company_id"]


def ensure_company(user):
    if not user["company_id"]:
        raise ApiError("NO_COMPANY: هذا المستخدم غير مرتبط بشركة / User is not linked to a company", 403)
    return user["company_id"]


def q1(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def qall(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def clean_filters(qs, allowed):
    return {k: qs.get(k) for k in allowed if qs.get(k)}


# ==========================================================================
# route table
# ==========================================================================
ROUTES = []


def route(method, pattern, permission=None):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, permission, fn))
        return fn
    return deco


# ------------------------------ auth --------------------------------------
@route("POST", r"/api/auth/login")
def login(ctx):
    body = ctx.body
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    conn = ctx.conn
    row = q1(conn, "SELECT * FROM users WHERE lower(username)=?", (username,))
    if row is None or not verify_password(password, row["password_hash"]):
        if row:
            attempts = row["failed_attempts"] + 1
            conn.execute("UPDATE users SET failed_attempts=? WHERE id=?", (attempts, row["id"]))
            if attempts >= 8:
                conn.execute("UPDATE users SET locked_until=? WHERE id=?",
                             ((datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
        raise ApiError("INVALID_CREDENTIALS: بيانات الدخول غير صحيحة / Invalid username or password", 401)
    if not row["is_active"]:
        raise ApiError("INACTIVE: الحساب موقوف / Account disabled", 403)
    if row["locked_until"] and row["locked_until"] > utcnow():
        raise ApiError("LOCKED: الحساب مقفل مؤقتًا بسبب محاولات دخول متكررة / Account temporarily locked", 423)
    conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login_at=? WHERE id=?",
                 (utcnow(), row["id"]))
    raw, hashed = issue_token(row["id"])
    from security import session_expiry
    conn.execute("INSERT INTO sessions (token, user_id, expires_at, ip, user_agent) VALUES (?,?,?,?,?)",
                 (hashed, row["id"], session_expiry(), ctx.ip, ctx.headers.get("User-Agent", "")[:200]))
    user = load_user(conn, row["id"])
    audit(conn, user, "auth.login", "user", row["id"], {"ip": ctx.ip})
    return {"token": raw, "user": public_user(user)}


@route("POST", r"/api/auth/logout")
def logout(ctx):
    ctx.conn.execute("UPDATE sessions SET revoked=1 WHERE token=?", (ctx.token_hash,))
    return {"ok": True}


@route("GET", r"/api/auth/me")
def me(ctx):
    return {"user": public_user(ctx.user)}


@route("PATCH", r"/api/auth/preferences")
def preferences(ctx):
    lang = ctx.body.get("language")
    theme = ctx.body.get("theme")
    if lang not in (None, "ar", "en"):
        raise ApiError("INVALID_LANGUAGE")
    if theme not in (None, "light", "dark", "system"):
        raise ApiError("INVALID_THEME")
    if lang:
        ctx.conn.execute("UPDATE users SET language=? WHERE id=?", (lang, ctx.user["id"]))
    if theme:
        ctx.conn.execute("UPDATE users SET theme=? WHERE id=?", (theme, ctx.user["id"]))
    return {"ok": True}


@route("POST", r"/api/auth/change-password")
def change_password(ctx):
    old = ctx.body.get("current_password") or ""
    new = ctx.body.get("new_password") or ""
    if len(new) < 10:
        raise ApiError("WEAK_PASSWORD: كلمة المرور يجب ألا تقل عن 10 أحرف / Password must be at least 10 characters")
    row = q1(ctx.conn, "SELECT password_hash FROM users WHERE id=?", (ctx.user["id"],))
    if not verify_password(old, row["password_hash"]):
        raise ApiError("INVALID_CREDENTIALS: كلمة المرور الحالية غير صحيحة / Current password incorrect", 401)
    from security import hash_password
    ctx.conn.execute("UPDATE users SET password_hash=?, password_must_change=0 WHERE id=?",
                     (hash_password(new), ctx.user["id"]))
    audit(ctx.conn, ctx.user, "auth.change_password", "user", ctx.user["id"])
    return {"ok": True}


# ------------------------------ meta --------------------------------------
# ---------------------------- editing (PATCH) -----------------------------
def _patch(ctx, fn, record_id, **kwargs):
    cid = ensure_company(ctx.user)
    return fn(ctx.conn, ctx.user, cid, int(record_id), ctx.body, ip=ctx.ip, **kwargs)


@route("PATCH", r"/api/customers/(\d+)", "customers.edit")
def customers_update(ctx, customer_id):
    return _patch(ctx, lambda c, u, cid, rid, body, ip=None: editing.update_master(
        c, u, cid, "customers", rid, body, ip=ip), customer_id)


@route("PATCH", r"/api/vendors/(\d+)", "vendors.edit")
def vendors_update(ctx, vendor_id):
    return _patch(ctx, lambda c, u, cid, rid, body, ip=None: editing.update_master(
        c, u, cid, "vendors", rid, body, ip=ip), vendor_id)


@route("PATCH", r"/api/products/(\d+)", "inventory.edit")
def products_update(ctx, product_id):
    return _patch(ctx, lambda c, u, cid, rid, body, ip=None: editing.update_master(
        c, u, cid, "products", rid, body, ip=ip), product_id)


@route("PATCH", r"/api/employees/(\d+)", "payroll.edit")
def employees_update(ctx, employee_id):
    return _patch(ctx, lambda c, u, cid, rid, body, ip=None: editing.update_master(
        c, u, cid, "employees", rid, body, ip=ip), employee_id)


@route("PATCH", r"/api/assets/(\d+)", "assets.edit")
def assets_update(ctx, asset_id):
    return _patch(ctx, lambda c, u, cid, rid, body, ip=None: editing.update_master(
        c, u, cid, "fixed-assets", rid, body, ip=ip), asset_id)


@route("PATCH", r"/api/bank-accounts/(\d+)", "banks.edit")
def bank_accounts_update(ctx, account_id):
    return _patch(ctx, lambda c, u, cid, rid, body, ip=None: editing.update_master(
        c, u, cid, "bank-accounts", rid, body, ip=ip), account_id)


@route("PATCH", r"/api/bank-transactions/(\d+)", "banks.edit")
def bank_transactions_update(ctx, txn_id):
    return _patch(ctx, editing.update_bank_transaction, txn_id)


@route("PATCH", r"/api/budgets/(\d+)", "budgets.edit")
def budgets_update(ctx, budget_id):
    return _patch(ctx, editing.update_budget, budget_id)


@route("PATCH", r"/api/sales-invoices/(\d+)", "sales.edit")
def sales_invoice_update(ctx, invoice_id):
    return _patch(ctx, editing.update_sales_invoice, invoice_id)


@route("PATCH", r"/api/purchase-invoices/(\d+)", "purchases.edit")
def purchase_invoice_update(ctx, invoice_id):
    return _patch(ctx, editing.update_purchase_invoice, invoice_id)


@route("PATCH", r"/api/journal-entries/(\d+)", "journal.edit")
def journal_entry_update(ctx, entry_id):
    return _patch(ctx, editing.update_journal_entry, entry_id)


@route("PATCH", r"/api/payments/(\d+)", "payments.edit")
def payment_update(ctx, payment_id):
    return _patch(ctx, editing.update_payment, payment_id)


# create a bank / cash box (the Banks page used to be read-only for accounts)
@route("POST", r"/api/bank-accounts", "banks.manage")
def bank_accounts_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("code", "name_ar", "name_en", "account_type", "gl_account_id"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    if b["account_type"] not in ("bank", "cash"):
        raise ApiError("INVALID_VALUE: نوع الحساب يجب أن يكون bank أو cash / invalid account_type")
    if q1(ctx.conn, "SELECT 1 FROM bank_accounts WHERE company_id=? AND code=?", (cid, b["code"])):
        raise ApiError("DUPLICATE: كود الحساب مستخدم / Account code already exists")
    gl = q1(ctx.conn, "SELECT id FROM accounts WHERE id=? AND company_id=?",
            (int(b["gl_account_id"]), cid))
    if gl is None:
        raise ApiError("NOT_FOUND: حساب الأستاذ غير موجود / GL account not found", 404)
    cur = ctx.conn.execute("""INSERT INTO bank_accounts (company_id, code, name_ar, name_en, account_type,
        gl_account_id, iban, currency) VALUES (?,?,?,?,?,?,?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b["account_type"], int(b["gl_account_id"]),
         b.get("iban"), b.get("currency") or "EGP"))
    audit(ctx.conn, ctx.user, "bank_account.create", "bank_account", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


# ---------------------------- deletions -----------------------------------
@route("DELETE", r"/api/sales-invoices/(\d+)", "sales.delete")
def sales_invoice_delete(ctx, invoice_id):
    return _delete_document(ctx, deletion.delete_sales_invoice, int(invoice_id))


@route("DELETE", r"/api/purchase-invoices/(\d+)", "purchases.delete")
def purchase_invoice_delete(ctx, invoice_id):
    return _delete_document(ctx, deletion.delete_purchase_invoice, int(invoice_id))


@route("DELETE", r"/api/payments/(\d+)", "payments.delete")
def payment_delete(ctx, payment_id):
    return _delete_document(ctx, deletion.delete_payment, int(payment_id))


@route("DELETE", r"/api/journal-entries/(\d+)", "journal.delete")
def journal_entry_delete(ctx, entry_id):
    return _delete_document(ctx, deletion.delete_journal_entry, int(entry_id))


@route("DELETE", r"/api/products/(\d+)", "inventory.manage")
def product_delete(ctx, product_id):
    return _delete_document(ctx, deletion.delete_product, int(product_id))


@route("DELETE", r"/api/bank-transactions/(\d+)", "banks.manage")
def bank_transaction_delete(ctx, txn_id):
    return _delete_document(ctx, deletion.delete_bank_transaction, int(txn_id))


@route("DELETE", r"/api/inventory/movements/(\d+)", "inventory.manage")
def stock_movement_delete(ctx, movement_id):
    return _delete_document(ctx, deletion.delete_stock_movement, int(movement_id))


@route("DELETE", r"/api/assets/(\d+)", "assets.manage")
def asset_delete(ctx, asset_id):
    return _delete_document(ctx, deletion.delete_asset, int(asset_id))


@route("DELETE", r"/api/employees/(\d+)", "payroll.manage")
def employee_delete(ctx, employee_id):
    return _delete_document(ctx, deletion.delete_employee, int(employee_id))


@route("DELETE", r"/api/customers/(\d+)", "customers.delete")
def customer_delete(ctx, customer_id):
    return _delete_document(ctx, deletion.delete_party, int(customer_id), kind="customer")


@route("DELETE", r"/api/vendors/(\d+)", "vendors.delete")
def vendor_delete(ctx, vendor_id):
    return _delete_document(ctx, deletion.delete_party, int(vendor_id), kind="vendor")


def _delete_document(ctx, fn, record_id, **kwargs):
    """Shared plumbing: company scope + optimistic-lock guard + audit IP."""
    cid = ensure_company(ctx.user)
    if str(ctx.body.get("confirm", "")).lower() not in ("1", "true", "yes"):
        raise ApiError("CONFIRM_REQUIRED: يجب تأكيد الحذف / Deletion must be confirmed", 428)
    result = fn(ctx.conn, ctx.user, cid, record_id, ip=ctx.ip, **kwargs)
    label = result.get("number") or result.get("code") or record_id
    notify(ctx.conn, cid, ctx.user["id"], "delete",
           "تم الحذف", "Deleted", f"تم حذف {label}", f"Deleted {label}")
    return result


@route("GET", r"/api/dev/accounts", None)
def dev_accounts(ctx):
    """DEMO ONLY — returns the seeded development logins so the login screen can
    offer one-click sign-in. Disabled when ERP_SEED_DEMO_DATA=false."""
    if os.environ.get("ERP_SEED_DEMO_DATA", "true").lower() in ("0", "false", "no"):
        raise ApiError("NOT_FOUND: غير متاح / not available", 404)
    path = Path(os.environ.get("ERP_CREDENTIALS_FILE") or (BASE_DIR / "data" / "dev-credentials.txt"))
    conn = read()
    users = {r["username"]: r for r in
             conn.execute("SELECT username, full_name_ar, role_code FROM users").fetchall()}
    role_names = {r["code"]: (r["name_ar"], r["name_en"]) for r in
                  conn.execute("SELECT code, name_ar, name_en FROM roles").fetchall()}
    accounts = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) != 4:
                continue
            name, username, password, role = parts
            rn = role_names.get(role, (role, role))
            accounts.append({"name_ar": name, "username": username, "password": password,
                             "role": role, "role_name_ar": rn[0], "role_name_en": rn[1]})
    else:
        # Read-only filesystem (serverless): rebuild the list from the seed table.
        try:
            from seed import DEV_PASSWORDS
        except Exception:
            DEV_PASSWORDS = {}
        for username, password in DEV_PASSWORDS.items():
            row = users.get(username)
            if row is None:
                continue
            rn = role_names.get(row["role_code"], (row["role_code"], row["role_code"]))
            accounts.append({"name_ar": row["full_name_ar"], "username": username,
                             "password": password, "role": row["role_code"],
                             "role_name_ar": rn[0], "role_name_en": rn[1]})
    if not accounts:
        raise ApiError("NOT_FOUND: ملف بيانات التطوير غير موجود / dev credentials missing", 404)
    return {"accounts": accounts, "demo": True,
            "warning": "Development credentials — never enable this endpoint in production."}


@route("GET", r"/api/meta")
def meta(ctx):
    conn = ctx.conn
    cid = ctx.user["company_id"]
    return {
        "companies": qall(conn, "SELECT id, code, name_ar, name_en FROM companies"),
        "branches": qall(conn, "SELECT id, code, name_ar, name_en FROM branches WHERE company_id=?", (cid,)) if cid else [],
        "cost_centers": qall(conn, "SELECT id, code, name_ar, name_en FROM cost_centers WHERE company_id=?", (cid,)) if cid else [],
        "projects": qall(conn, "SELECT id, code, name_ar, name_en FROM projects WHERE company_id=?", (cid,)) if cid else [],
        "warehouses": qall(conn, "SELECT id, code, name_ar, name_en FROM warehouses WHERE company_id=?", (cid,)) if cid else [],
        "fiscal_years": qall(conn, "SELECT * FROM fiscal_years WHERE company_id=? ORDER BY start_date DESC", (cid,)) if cid else [],
        "roles": qall(conn, "SELECT code, name_ar, name_en, permissions, data_scope FROM roles"),
        "today": date.today().isoformat(),
        "currency": "EGP",
    }


@route("GET", r"/api/health")
def health(ctx):
    # dev_mode tells the login screen it may show the demo accounts panel.
    dev_mode = os.environ.get("ERP_SEED_DEMO_DATA", "true").lower() not in ("0", "false", "no")
    return {"status": "ok", "time": utcnow(), "version": "1.1.0",
            "features": {"pdf": True, "xlsx": True, "einvoice": True},
            "dev_mode": dev_mode}


# ---------------------------- dashboard -----------------------------------
# ---------------------------- company settings -----------------------------
COMPANY_SETTING_KEYS = ("company_email", "company_website", "bank_details", "invoice_footer",
                        "default_tax_rate_bp", "invoice_terms")


@route("GET", r"/api/company", "reports.view")
def company_get(ctx):
    """Company profile used on invoices, statements and PDF headers."""
    cid = ensure_company(ctx.user)
    co = q1(ctx.conn, "SELECT * FROM companies WHERE id=?", (cid,))
    settings = {r["key"]: r["value"] for r in
                qall(ctx.conn, "SELECT key, value FROM settings WHERE company_id=?", (cid,))}
    return {"company": dict(co), "settings": settings,
            "can_edit": has(ctx.user, "settings.manage")}


@route("PATCH", r"/api/company", "settings.manage")
def company_update(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    # Only touch the columns actually present in the request (partial update).
    COLUMNS = ("name_ar", "name_en", "tax_number", "address", "phone")
    sets, params = [], []
    for col in COLUMNS:
        if col in b:
            value = str(b.get(col) or "").strip()
            if col in ("name_ar", "name_en") and not value:
                raise ApiError("MISSING_FIELD: اسم الشركة مطلوب / company name required")
            sets.append(f"{col}=?")
            params.append(value or None)
    if sets:
        params.append(cid)
        ctx.conn.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", tuple(params))
    for key in COMPANY_SETTING_KEYS:
        if key in b:
            ctx.conn.execute("""INSERT INTO settings (company_id, key, value) VALUES (?,?,?)
                                ON CONFLICT(company_id, key) DO UPDATE SET value=excluded.value""",
                             (cid, key, str(b[key]).strip()))
    changed = {k: v for k, v in b.items() if k in
               ("name_ar", "name_en", "tax_number", "address", "phone") + COMPANY_SETTING_KEYS}
    audit(ctx.conn, ctx.user, "settings.company", "company", cid, changed, company_id=cid)
    return {"ok": True, "changed": sorted(changed)}


@route("GET", r"/api/dashboard", "dashboard.view")
def dashboard(ctx):
    cid = ensure_company(ctx.user)
    as_of = ctx.qs.get("as_of") or date.today().isoformat()
    return reports.dashboard(ctx.conn, cid, as_of)


# ---------------------------- accounts ------------------------------------
@route("GET", r"/api/accounts", "accounts.view")
def accounts_list(ctx):
    cid = ensure_company(ctx.user)
    rows = qall(ctx.conn, """SELECT a.*, p.code AS parent_code,
            (SELECT COALESCE(SUM(debit_cents-credit_cents),0) FROM v_posted_lines l
             WHERE l.account_id=a.id) AS balance_cents
        FROM accounts a LEFT JOIN accounts p ON p.id=a.parent_id
        WHERE a.company_id=? ORDER BY a.code""", (cid,))
    # Natural (presentation) sign: debit-nature accounts keep their sign, credit-nature ones are flipped
    for r in rows:
        r["display_balance_cents"] = r["balance_cents"] if r["type"] in ("asset", "expense") else -r["balance_cents"]
    return {"accounts": rows}


@route("POST", r"/api/accounts", "accounts.create")
def accounts_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("code", "name_ar", "name_en", "type"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: الحقل {f} مطلوب / Field {f} is required")
    if b["type"] not in ("asset", "liability", "equity", "revenue", "expense"):
        raise ApiError("INVALID_TYPE: نوع الحساب غير صحيح / Invalid account type")
    if q1(ctx.conn, "SELECT 1 FROM accounts WHERE company_id=? AND code=?", (cid, b["code"])):
        raise ApiError("DUPLICATE_CODE: كود الحساب مستخدم بالفعل / Account code already exists")
    parent_id = b.get("parent_id")
    level = 1
    if parent_id:
        p = q1(ctx.conn, "SELECT * FROM accounts WHERE id=? AND company_id=?", (parent_id, cid))
        if p is None:
            raise ApiError("INVALID_PARENT: الحساب الأب غير موجود / Parent account not found")
        level = p["level"] + 1
        ctx.conn.execute("UPDATE accounts SET is_postable=0 WHERE id=?", (parent_id,))
    cur = ctx.conn.execute(
        """INSERT INTO accounts (company_id, code, name_ar, name_en, type, parent_id, level, is_postable,
              is_active, currency, notes, created_by) VALUES (?,?,?,?,?,?,?,?,1,'EGP',?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b["type"], parent_id, level,
         1 if b.get("is_postable", True) else 0, b.get("notes"), ctx.user["id"]))
    audit(ctx.conn, ctx.user, "accounts.create", "account", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("PATCH", r"/api/accounts/(\d+)", "accounts.create")
def accounts_update(ctx, account_id):
    cid = ensure_company(ctx.user)
    acc = q1(ctx.conn, "SELECT * FROM accounts WHERE id=? AND company_id=?", (account_id, cid))
    if acc is None:
        raise ApiError("NOT_FOUND: الحساب غير موجود / Account not found", 404)
    b = ctx.body
    if b.get("is_active") is not None and not b["is_active"]:
        used = q1(ctx.conn, "SELECT COUNT(*) c FROM journal_lines WHERE account_id=?", (account_id,))["c"]
        if used:
            raise ApiError("ACCOUNT_IN_USE: لا يمكن تعطيل حساب مستخدم في قيود / Account used in entries cannot be deactivated")
    fields = {k: b[k] for k in ("name_ar", "name_en", "is_active", "is_postable", "notes") if k in b}
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        ctx.conn.execute(f"UPDATE accounts SET {sets} WHERE id=?", (*fields.values(), account_id))
    audit(ctx.conn, ctx.user, "accounts.update", "account", account_id, b, company_id=cid)
    return {"ok": True}


@route("DELETE", r"/api/accounts/(\d+)", "accounts.create")
def accounts_delete(ctx, account_id):
    cid = ensure_company(ctx.user)
    used = q1(ctx.conn, "SELECT COUNT(*) c FROM journal_lines WHERE account_id=?", (account_id,))["c"]
    if used:
        raise ApiError("ACCOUNT_IN_USE: لا يمكن حذف حساب مستخدم في قيود / Cannot delete an account used by entries")
    ctx.conn.execute("DELETE FROM accounts WHERE id=? AND company_id=?", (account_id, cid))
    audit(ctx.conn, ctx.user, "accounts.delete", "account", account_id, company_id=cid)
    return {"ok": True}


# -------------------------- journal entries -------------------------------
@route("GET", r"/api/journal-entries", "journal.view")
def journal_list(ctx):
    cid = ensure_company(ctx.user)
    status = ctx.qs.get("status")
    date_from = ctx.qs.get("from")
    date_to = ctx.qs.get("to")
    search = ctx.qs.get("q")
    page = max(1, int(ctx.qs.get("page", 1)))
    per_page = min(200, max(10, int(ctx.qs.get("per_page", 25))))
    where = ["e.company_id=?"]
    params = [cid]
    if status and status != "all":
        where.append("e.status=?")
        params.append(status)
    if date_from:
        where.append("e.entry_date>=?")
        params.append(date_from)
    if date_to:
        where.append("e.entry_date<=?")
        params.append(date_to)
    if search:
        where.append("(e.memo LIKE ? OR e.entry_no LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    w = " AND ".join(where)
    total = q1(ctx.conn, f"SELECT COUNT(*) c FROM journal_entries e WHERE {w}", tuple(params))["c"]
    rows = qall(ctx.conn, f"""SELECT e.*, u.full_name_ar AS created_by_name, b.name_ar AS branch_name
        FROM journal_entries e LEFT JOIN users u ON u.id=e.created_by
        LEFT JOIN branches b ON b.id=e.branch_id
        WHERE {w} ORDER BY e.entry_date DESC, e.id DESC LIMIT ? OFFSET ?""",
        tuple(params) + (per_page, (page - 1) * per_page))
    return {"entries": rows, "total": total, "page": page, "per_page": per_page}


@route("GET", r"/api/journal-entries/(\d+)", "journal.view")
def journal_get(ctx, entry_id):
    cid = ensure_company(ctx.user)
    e = q1(ctx.conn, "SELECT * FROM journal_entries WHERE id=? AND company_id=?", (entry_id, cid))
    if e is None:
        raise ApiError("NOT_FOUND: القيد غير موجود / Entry not found", 404)
    lines = qall(ctx.conn, """SELECT l.*, a.code AS account_code, a.name_ar AS account_ar, a.name_en AS account_en
        FROM journal_lines l JOIN accounts a ON a.id=l.account_id WHERE l.entry_id=? ORDER BY l.line_no""", (entry_id,))
    return {"entry": dict(e), "lines": lines}


@route("POST", r"/api/journal-entries", "journal.create")
def journal_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    if not b.get("memo"):
        raise ApiError("MISSING_MEMO: بيان القيد مطلوب / Entry memo is required")
    lines = []
    for ln in b.get("lines", []):
        lines.append({
            "account_id": int(ln["account_id"]),
            "debit_cents": to_cents(ln.get("debit") or 0),
            "credit_cents": to_cents(ln.get("credit") or 0),
            "description": ln.get("description"),
            "cost_center_id": ln.get("cost_center_id") or b.get("cost_center_id"),
            "project_id": ln.get("project_id") or b.get("project_id"),
        })
    entry_id = accounting.create_entry(
        ctx.conn, ctx.user, company_id=cid, entry_date=b.get("entry_date") or date.today().isoformat(),
        memo=b["memo"], lines=lines, entry_type=b.get("entry_type", "manual"),
        branch_id=b.get("branch_id") or ctx.user["branch_id"],
        cost_center_id=b.get("cost_center_id"), project_id=b.get("project_id"),
        idempotency_key=b.get("idempotency_key"))
    return {"id": entry_id}


@route("POST", r"/api/journal-entries/(\d+)/submit", "journal.create")
def journal_submit(ctx, entry_id):
    cid = ensure_company(ctx.user)
    return accounting.submit_entry(ctx.conn, ctx.user, entry_id, cid, ctx.body.get("comment", ""))


@route("POST", r"/api/journal-entries/(\d+)/review", "journal.review")
def journal_review(ctx, entry_id):
    cid = ensure_company(ctx.user)
    return accounting.review_entry(ctx.conn, ctx.user, entry_id, cid,
                                   ctx.body.get("decision", "approve"), ctx.body.get("comment", ""))


@route("POST", r"/api/journal-entries/(\d+)/approve", "journal.approve")
def journal_approve(ctx, entry_id):
    cid = ensure_company(ctx.user)
    return accounting.approve_entry(ctx.conn, ctx.user, entry_id, cid, ctx.body.get("comment", ""))


@route("POST", r"/api/journal-entries/(\d+)/post", "journal.post")
def journal_post(ctx, entry_id):
    cid = ensure_company(ctx.user)
    return accounting.post_entry(ctx.conn, ctx.user, entry_id, cid)


@route("POST", r"/api/journal-entries/(\d+)/reverse", "journal.reverse")
def journal_reverse(ctx, entry_id):
    cid = ensure_company(ctx.user)
    return accounting.reverse_entry(ctx.conn, ctx.user, entry_id, cid,
                                    ctx.body.get("date"), ctx.body.get("reason", ""))


# ---------------------------- master data ---------------------------------
@route("GET", r"/api/customers", "customers.view")
def customers_list(ctx):
    cid = ensure_company(ctx.user)
    rows = qall(ctx.conn, """SELECT c.*,
            COALESCE((SELECT SUM(i.total_cents-i.paid_cents) FROM sales_invoices i
                      WHERE i.customer_id=c.id AND i.status IN ('posted','partially_paid')),0) AS outstanding_cents,
            COALESCE((SELECT SUM(i.total_cents) FROM sales_invoices i WHERE i.customer_id=c.id
                      AND i.status IN ('posted','partially_paid','paid')),0) AS invoiced_cents
        FROM customers c WHERE c.company_id=? ORDER BY c.code""", (cid,))
    return {"customers": rows}


@route("POST", r"/api/customers", "customers.create")
def customers_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("code", "name_ar", "name_en"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    if q1(ctx.conn, "SELECT 1 FROM customers WHERE company_id=? AND code=?", (cid, b["code"])):
        raise ApiError("DUPLICATE: كود العميل مستخدم / Customer code already exists")
    ar_acc = q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='1210'", (cid,))["id"]
    cur = ctx.conn.execute("""INSERT INTO customers (company_id, code, name_ar, name_en, phone, email, tax_number,
        credit_limit_cents, payment_terms_days, account_id, opening_balance_cents)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b.get("phone"), b.get("email"), b.get("tax_number"),
         to_cents(b.get("credit_limit") or 0), int(b.get("payment_terms_days") or 30), ar_acc,
         to_cents(b.get("opening_balance") or 0)))
    audit(ctx.conn, ctx.user, "customers.create", "customer", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("GET", r"/api/vendors", "vendors.view")
def vendors_list(ctx):
    cid = ensure_company(ctx.user)
    rows = qall(ctx.conn, """SELECT v.*,
            COALESCE((SELECT SUM(i.total_cents-i.paid_cents) FROM purchase_invoices i
                      WHERE i.vendor_id=v.id AND i.status IN ('posted','partially_paid')),0) AS outstanding_cents
        FROM vendors v WHERE v.company_id=? ORDER BY v.code""", (cid,))
    return {"vendors": rows}


@route("POST", r"/api/vendors", "vendors.create")
def vendors_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("code", "name_ar", "name_en"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    if q1(ctx.conn, "SELECT 1 FROM vendors WHERE company_id=? AND code=?", (cid, b["code"])):
        raise ApiError("DUPLICATE: كود المورد مستخدم / Vendor code already exists")
    ap_acc = q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='2110'", (cid,))["id"]
    cur = ctx.conn.execute("""INSERT INTO vendors (company_id, code, name_ar, name_en, phone, email, tax_number,
        payment_terms_days, account_id) VALUES (?,?,?,?,?,?,?,?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b.get("phone"), b.get("email"), b.get("tax_number"),
         int(b.get("payment_terms_days") or 30), ap_acc))
    audit(ctx.conn, ctx.user, "vendors.create", "vendor", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("GET", r"/api/vendors/(\d+)/statement", "vendors.view")
def vendor_statement(ctx, vendor_id):
    cid = ensure_company(ctx.user)
    v = q1(ctx.conn, "SELECT * FROM vendors WHERE id=? AND company_id=?", (vendor_id, cid))
    if v is None:
        raise ApiError("NOT_FOUND: المورد غير موجود / Vendor not found", 404)
    invs = qall(ctx.conn, """SELECT id, invoice_no, invoice_date, due_date, total_cents, paid_cents, status
        FROM purchase_invoices WHERE vendor_id=? AND company_id=? ORDER BY invoice_date""", (vendor_id, cid))
    pays = qall(ctx.conn, """SELECT p.id, p.payment_no, p.payment_date, p.amount_cents, p.reference
        FROM payments p WHERE p.party_type='vendor' AND p.party_id=? AND p.company_id=? AND p.status='posted'
        ORDER BY p.payment_date""", (vendor_id, cid))
    return {"vendor": dict(v), "invoices": invs, "payments": pays}


@route("GET", r"/api/products", "inventory.view")
def products_list(ctx):
    cid = ensure_company(ctx.user)
    rows = qall(ctx.conn, """SELECT p.*,
        COALESCE((SELECT SUM(qty_milli) FROM stock_movements s WHERE s.product_id=p.id),0) AS qty_milli,
        COALESCE((SELECT SUM(value_cents) FROM stock_movements s WHERE s.product_id=p.id),0) AS stock_value_cents
        FROM products p WHERE p.company_id=? ORDER BY p.sku""", (cid,))
    return {"products": rows}


@route("POST", r"/api/products", "inventory.manage")
def products_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("sku", "name_ar", "name_en"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    cur = ctx.conn.execute("""INSERT INTO products (company_id, sku, name_ar, name_en, unit, category, is_service,
        sale_price_cents, purchase_price_cents, tax_rate_bp, reorder_level_milli, inventory_account_id,
        cogs_account_id, revenue_account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, b["sku"], b["name_ar"], b["name_en"], b.get("unit") or "قطعة", b.get("category"),
         1 if b.get("is_service") else 0, to_cents(b.get("sale_price") or 0),
         to_cents(b.get("purchase_price") or 0), int(b.get("tax_rate_bp") or 1400),
         to_milli(b.get("reorder_level") or 0),
         q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='1310'", (cid,))["id"],
         q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='5100'", (cid,))["id"],
         q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='4100'", (cid,))["id"]))
    audit(ctx.conn, ctx.user, "products.create", "product", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


# ------------------------------- sales ------------------------------------
@route("GET", r"/api/sales-invoices", "sales.view")
def sales_list(ctx):
    cid = ensure_company(ctx.user)
    status = ctx.qs.get("status")
    where = "i.company_id=?"
    params = [cid]
    if status and status != "all":
        where += " AND i.status=?"
        params.append(status)
    rows = qall(ctx.conn, f"""SELECT i.*, c.name_ar AS customer_ar, c.name_en AS customer_en, c.code AS customer_code,
            u.full_name_ar AS created_by_name
        FROM sales_invoices i JOIN customers c ON c.id=i.customer_id
        LEFT JOIN users u ON u.id=i.created_by
        WHERE {where} ORDER BY i.invoice_date DESC, i.id DESC LIMIT 300""", tuple(params))
    return {"invoices": rows}


@route("GET", r"/api/sales-invoices/(\d+)", "sales.view")
def sales_get(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    inv = q1(ctx.conn, "SELECT i.*, c.name_ar AS customer_ar, c.name_en AS customer_en, "
                       "u.full_name_ar AS created_by_name FROM sales_invoices i "
                       "JOIN customers c ON c.id=i.customer_id LEFT JOIN users u ON u.id=i.created_by "
                       "WHERE i.id=? AND i.company_id=?", (invoice_id, cid))
    if inv is None:
        raise ApiError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", 404)
    lines = qall(ctx.conn, """SELECT l.*, p.name_ar AS product_ar, p.sku FROM sales_invoice_lines l
        LEFT JOIN products p ON p.id=l.product_id WHERE l.invoice_id=? ORDER BY l.line_no""", (invoice_id,))
    allocs = qall(ctx.conn, """SELECT a.*, p.payment_no, p.payment_date FROM payment_allocations a
        JOIN payments p ON p.id=a.payment_id WHERE a.invoice_kind='sales' AND a.invoice_id=?""", (invoice_id,))
    return {"invoice": dict(inv), "lines": lines, "allocations": allocs}


@route("POST", r"/api/sales-invoices", "sales.create")
def sales_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    lines = [{"product_id": ln.get("product_id"), "description": ln.get("description"),
              "qty_milli": to_milli(ln.get("qty")), "unit_price_cents": to_cents(ln.get("unit_price")),
              "discount_cents": to_cents(ln.get("discount") or 0),
              "tax_rate_bp": int(ln.get("tax_rate_bp", 1400))} for ln in b.get("lines", [])]
    if not lines:
        raise ApiError("EMPTY: الفاتورة لا تحتوي على سطور / Invoice has no lines")
    inv_id = documents.create_sales_invoice(ctx.conn, ctx.user, cid, {
        "customer_id": int(b["customer_id"]), "invoice_date": b.get("invoice_date") or date.today().isoformat(),
        "due_date": b.get("due_date"), "warehouse_id": b.get("warehouse_id"),
        "branch_id": b.get("branch_id") or ctx.user["branch_id"], "lines": lines,
        "notes": b.get("notes"), "idempotency_key": b.get("idempotency_key"),
    })
    if b.get("post"):
        documents.post_sales_invoice(ctx.conn, ctx.user, cid, inv_id)
    return {"id": inv_id}


@route("POST", r"/api/sales-invoices/(\d+)/post", "sales.post")
def sales_post(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    return documents.post_sales_invoice(ctx.conn, ctx.user, cid, invoice_id)


# ----------------------------- purchases ----------------------------------
@route("GET", r"/api/purchase-invoices", "purchases.view")
def purchases_list(ctx):
    cid = ensure_company(ctx.user)
    status = ctx.qs.get("status")
    where = "i.company_id=?"
    params = [cid]
    if status and status != "all":
        where += " AND i.status=?"
        params.append(status)
    rows = qall(ctx.conn, f"""SELECT i.*, v.name_ar AS vendor_ar, v.name_en AS vendor_en, v.code AS vendor_code
        FROM purchase_invoices i JOIN vendors v ON v.id=i.vendor_id
        WHERE {where} ORDER BY i.invoice_date DESC, i.id DESC LIMIT 300""", tuple(params))
    return {"invoices": rows}


@route("GET", r"/api/purchase-invoices/(\d+)", "purchases.view")
def purchase_get(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    inv = q1(ctx.conn, """SELECT i.*, v.name_ar AS vendor_ar, v.name_en AS vendor_en,
            u.full_name_ar AS created_by_name
        FROM purchase_invoices i JOIN vendors v ON v.id=i.vendor_id
        LEFT JOIN users u ON u.id=i.created_by
        WHERE i.id=? AND i.company_id=?""", (invoice_id, cid))
    if inv is None:
        raise ApiError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", 404)
    lines = qall(ctx.conn, """SELECT l.*, p.name_ar AS product_ar, p.sku FROM purchase_invoice_lines l
        LEFT JOIN products p ON p.id=l.product_id WHERE l.invoice_id=? ORDER BY l.line_no""", (invoice_id,))
    return {"invoice": dict(inv), "lines": lines}


@route("POST", r"/api/purchase-invoices", "purchases.create")
def purchases_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    lines = [{"product_id": ln.get("product_id"), "description": ln.get("description"),
              "qty_milli": to_milli(ln.get("qty")), "unit_price_cents": to_cents(ln.get("unit_price")),
              "discount_cents": to_cents(ln.get("discount") or 0),
              "tax_rate_bp": int(ln.get("tax_rate_bp", 1400))} for ln in b.get("lines", [])]
    if not lines:
        raise ApiError("EMPTY: الفاتورة لا تحتوي على سطور / Invoice has no lines")
    inv_id = documents.create_purchase_invoice(ctx.conn, ctx.user, cid, {
        "vendor_id": int(b["vendor_id"]), "invoice_date": b.get("invoice_date") or date.today().isoformat(),
        "due_date": b.get("due_date"), "warehouse_id": b.get("warehouse_id"),
        "supplier_ref": b.get("supplier_ref"), "po_number": b.get("po_number"),
        "grn_number": b.get("grn_number"), "branch_id": b.get("branch_id") or ctx.user["branch_id"],
        "lines": lines, "notes": b.get("notes"), "idempotency_key": b.get("idempotency_key"),
    })
    if b.get("post"):
        documents.post_purchase_invoice(ctx.conn, ctx.user, cid, inv_id)
    return {"id": inv_id}


@route("POST", r"/api/purchase-invoices/(\d+)/post", "purchases.post")
def purchases_post(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    return documents.post_purchase_invoice(ctx.conn, ctx.user, cid, invoice_id)


# ------------------------------ payments ----------------------------------
@route("GET", r"/api/payments", "payments.view")
def payments_list(ctx):
    cid = ensure_company(ctx.user)
    rows = qall(ctx.conn, """SELECT p.*, a.code AS gl_code, a.name_ar AS gl_name,
            CASE WHEN p.party_type='customer' THEN (SELECT name_ar FROM customers WHERE id=p.party_id)
                 WHEN p.party_type='vendor' THEN (SELECT name_ar FROM vendors WHERE id=p.party_id) END AS party_name
        FROM payments p JOIN accounts a ON a.id=p.gl_account_id
        WHERE p.company_id=? ORDER BY p.payment_date DESC, p.id DESC LIMIT 300""", (cid,))
    return {"payments": rows}


@route("POST", r"/api/payments", "payments.create")
def payments_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    pay_id = documents.create_payment(ctx.conn, ctx.user, cid, {
        "payment_type": b.get("payment_type", "receipt"),
        "payment_date": b.get("payment_date") or date.today().isoformat(),
        "party_type": b.get("party_type"), "party_id": b.get("party_id"),
        "method": b.get("method", "bank"), "gl_account_id": int(b["gl_account_id"]),
        "amount_cents": to_cents(b.get("amount")),
        "branch_id": b.get("branch_id") or ctx.user["branch_id"],
        "reference": b.get("reference"), "notes": b.get("notes"),
        "allocations": [{"invoice_kind": a["invoice_kind"], "invoice_id": int(a["invoice_id"]),
                         "amount_cents": to_cents(a["amount"])} for a in b.get("allocations", [])],
        "idempotency_key": b.get("idempotency_key"),
    })
    if b.get("post"):
        documents.post_payment(ctx.conn, ctx.user, cid, pay_id)
    return {"id": pay_id}


@route("POST", r"/api/payments/(\d+)/post", "payments.post")
def payments_post(ctx, payment_id):
    cid = ensure_company(ctx.user)
    return documents.post_payment(ctx.conn, ctx.user, cid, payment_id)


# ------------------------- payables & receivables -------------------------
@route("GET", r"/api/payables", "payables.view")
def payables(ctx):
    cid = ensure_company(ctx.user)
    as_of = ctx.qs.get("as_of") or date.today().isoformat()
    return reports.vendor_payables(ctx.conn, cid, as_of)


@route("GET", r"/api/receivables", "customers.view")
def receivables(ctx):
    cid = ensure_company(ctx.user)
    as_of = ctx.qs.get("as_of") or date.today().isoformat()
    return {"position": reports.ar_position(ctx.conn, cid),
            "aging": reports.aging(ctx.conn, cid, as_of, "sales")}


# -------------------------------- banks -----------------------------------
@route("GET", r"/api/banks", "banks.view")
def banks(ctx):
    cid = ensure_company(ctx.user)
    return {"position": reports.cash_position(ctx.conn, cid),
            "accounts": qall(ctx.conn, """SELECT b.*, a.code AS gl_code FROM bank_accounts b
                JOIN accounts a ON a.id=b.gl_account_id WHERE b.company_id=?""", (cid,))}


@route("GET", r"/api/bank-transactions", "banks.view")
def bank_txns(ctx):
    cid = ensure_company(ctx.user)
    bank_id = ctx.qs.get("bank_account_id")
    where = "t.company_id=?"
    params = [cid]
    if bank_id:
        where += " AND t.bank_account_id=?"
        params.append(bank_id)
    rows = qall(ctx.conn, f"""SELECT t.*, b.name_ar AS bank_name, p.payment_no AS matched_payment_no
        FROM bank_transactions t JOIN bank_accounts b ON b.id=t.bank_account_id
        LEFT JOIN payments p ON p.id=t.matched_payment_id
        WHERE {where} ORDER BY t.txn_date DESC, t.id DESC LIMIT 300""", tuple(params))
    return {"transactions": rows}


@route("POST", r"/api/bank-transactions", "banks.manage")
def bank_txn_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    bank = q1(ctx.conn, "SELECT * FROM bank_accounts WHERE id=? AND company_id=?",
              (int(b["bank_account_id"]), cid))
    if bank is None:
        raise ApiError("NOT_FOUND: الحساب البنكي غير موجود / Bank account not found", 404)
    amount = to_cents(b.get("amount"))
    direction = b.get("direction", "debit")
    cur = ctx.conn.execute("""INSERT INTO bank_transactions (company_id, bank_account_id, txn_date, description,
        debit_cents, credit_cents, statement_ref) VALUES (?,?,?,?,?,?,?)""",
        (cid, bank["id"], b.get("txn_date") or date.today().isoformat(), b.get("description") or "حركة بنكية",
         amount if direction == "debit" else 0, amount if direction == "credit" else 0,
         b.get("statement_ref")))
    audit(ctx.conn, ctx.user, "bank_txn.create", "bank_transaction", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("POST", r"/api/bank-transactions/(\d+)/reconcile", "banks.manage")
def bank_reconcile(ctx, txn_id):
    cid = ensure_company(ctx.user)
    t = q1(ctx.conn, "SELECT * FROM bank_transactions WHERE id=? AND company_id=?", (txn_id, cid))
    if t is None:
        raise ApiError("NOT_FOUND: الحركة غير موجودة / Transaction not found", 404)
    payment_id = ctx.body.get("payment_id")
    if payment_id:
        p = q1(ctx.conn, "SELECT * FROM payments WHERE id=? AND company_id=? AND status='posted'", (payment_id, cid))
        if p is None:
            raise ApiError("NOT_FOUND: السند غير موجود أو غير مرحّل / Voucher not found or not posted", 404)
    ctx.conn.execute("""UPDATE bank_transactions SET is_reconciled=1, matched_payment_id=?, reconciled_by=?,
        reconciled_at=? WHERE id=?""", (payment_id, ctx.user["id"], utcnow(), txn_id))
    audit(ctx.conn, ctx.user, "bank_txn.reconcile", "bank_transaction", txn_id,
          {"payment_id": payment_id}, company_id=cid)
    return {"ok": True}


@route("GET", r"/api/bank-transactions/suggestions", "banks.manage")
def bank_suggestions(ctx):
    """Suggest matching posted payments for unreconciled bank transactions (never auto-confirms)."""
    cid = ensure_company(ctx.user)
    txns = qall(ctx.conn, """SELECT * FROM bank_transactions WHERE company_id=? AND is_reconciled=0
        ORDER BY txn_date""", (cid,))
    out = []
    for t in txns:
        amount = t["debit_cents"] or t["credit_cents"]
        candidates = qall(ctx.conn, """SELECT p.id, p.payment_no, p.payment_date, p.amount_cents, p.payment_type
            FROM payments p WHERE p.company_id=? AND p.status='posted' AND p.amount_cents=?
              AND NOT EXISTS (SELECT 1 FROM bank_transactions bt WHERE bt.matched_payment_id=p.id)
            ORDER BY abs(julianday(p.payment_date)-julianday(?)) LIMIT 3""",
            (cid, amount, t["txn_date"]))
        if candidates:
            out.append({"transaction": t, "candidates": candidates})
    return {"suggestions": out}


# ------------------------------ inventory ---------------------------------
@route("GET", r"/api/inventory", "inventory.view")
def inventory(ctx):
    cid = ensure_company(ctx.user)
    stock = qall(ctx.conn, """SELECT p.id AS product_id, p.sku, p.name_ar, p.name_en, p.unit, p.reorder_level_milli,
            w.id AS warehouse_id, w.name_ar AS warehouse_ar,
            COALESCE(SUM(s.qty_milli),0) AS qty_milli, COALESCE(SUM(s.value_cents),0) AS value_cents
        FROM products p CROSS JOIN warehouses w
        LEFT JOIN stock_movements s ON s.product_id=p.id AND s.warehouse_id=w.id
        WHERE p.company_id=? GROUP BY p.id, w.id ORDER BY p.sku, w.code""", (cid,))
    movements = qall(ctx.conn, """SELECT s.*, p.sku, p.name_ar AS product_ar, w.name_ar AS warehouse_ar
        FROM stock_movements s JOIN products p ON p.id=s.product_id JOIN warehouses w ON w.id=s.warehouse_id
        WHERE s.company_id=? ORDER BY s.id DESC LIMIT 150""", (cid,))
    totals = {"value_cents": sum(r["value_cents"] for r in stock)}
    return {"stock": stock, "movements": movements, "totals": totals}


@route("POST", r"/api/inventory/movements", "inventory.manage")
def inventory_move(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    product = q1(ctx.conn, "SELECT * FROM products WHERE id=? AND company_id=?",
                 (int(b["product_id"]), cid))
    warehouse = q1(ctx.conn, "SELECT * FROM warehouses WHERE id=? AND company_id=?",
                   (int(b["warehouse_id"]), cid))
    if product is None or warehouse is None:
        raise ApiError("NOT_FOUND: الصنف أو المستودع غير موجود / Product or warehouse not found", 404)
    qty = to_milli(b.get("qty"))
    move_type = b.get("move_type", "in")
    if move_type == "in":
        unit_cost = to_cents(b.get("unit_cost") or 0)
        if unit_cost <= 0:
            raise ApiError("INVALID_COST: تكلفة الوحدة مطلوبة / Unit cost is required")
        accounting.stock_in(ctx.conn, ctx.user, cid, product["id"], warehouse["id"], qty, unit_cost,
                            b.get("move_date") or date.today().isoformat(), notes=b.get("notes"))
    elif move_type == "out":
        accounting.stock_out(ctx.conn, ctx.user, cid, product["id"], warehouse["id"], qty,
                             b.get("move_date") or date.today().isoformat(), notes=b.get("notes"))
    elif move_type == "adjust":
        current_qty, _ = accounting.inventory_state(ctx.conn, cid, product["id"], warehouse["id"])
        diff = abs(qty) - abs(current_qty) if qty else -current_qty
        if diff > 0:
            unit_cost = to_cents(b.get("unit_cost") or product["purchase_price_cents"] / 100)
            accounting.stock_in(ctx.conn, ctx.user, cid, product["id"], warehouse["id"], diff, unit_cost,
                                b.get("move_date") or date.today().isoformat(), move_type="adjust",
                                notes=b.get("notes") or "تسوية جرد")
        elif diff < 0:
            accounting.stock_out(ctx.conn, ctx.user, cid, product["id"], warehouse["id"], -diff,
                                 b.get("move_date") or date.today().isoformat(), notes=b.get("notes") or "تسوية جرد")
    elif move_type == "transfer":
        target = q1(ctx.conn, "SELECT * FROM warehouses WHERE id=? AND company_id=?",
                    (int(b["target_warehouse_id"]), cid))
        if target is None:
            raise ApiError("NOT_FOUND: المستودع المستهدف غير موجود / Target warehouse not found", 404)
        value, avg = accounting.stock_out(ctx.conn, ctx.user, cid, product["id"], warehouse["id"], qty,
                                          b.get("move_date") or date.today().isoformat(),
                                          notes="تحويل مخزون — صادر")
        accounting.stock_in(ctx.conn, ctx.user, cid, product["id"], target["id"], qty, avg,
                            b.get("move_date") or date.today().isoformat(), notes="تحويل مخزون — وارد")
    else:
        raise ApiError("INVALID_TYPE: نوع الحركة غير معروف / Unknown movement type")
    audit(ctx.conn, ctx.user, "inventory.move", "product", product["id"], b, company_id=cid)
    return {"ok": True}


# ---------------------------- fixed assets --------------------------------
@route("GET", r"/api/assets", "assets.view")
def assets_list(ctx):
    cid = ensure_company(ctx.user)
    return {"assets": qall(ctx.conn, """SELECT a.*, ac.name_ar AS asset_account
        FROM fixed_assets a LEFT JOIN accounts ac ON ac.id=a.asset_account_id
        WHERE a.company_id=? ORDER BY a.code""", (cid,)),
        "runs": qall(ctx.conn, "SELECT * FROM depreciation_runs WHERE company_id=? ORDER BY id DESC", (cid,))}


@route("POST", r"/api/assets", "assets.manage")
def assets_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("code", "name_ar", "name_en", "cost", "useful_life_months"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    acc = q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code=?", (cid, b.get("asset_account_code", "1450")))
    cur = ctx.conn.execute("""INSERT INTO fixed_assets (company_id, code, name_ar, name_en, category, purchase_date,
        start_date, cost_cents, salvage_cents, useful_life_months, asset_account_id, dep_account_id, expense_account_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b.get("category"),
         b.get("purchase_date") or date.today().isoformat(), b.get("start_date") or date.today().isoformat(),
         to_cents(b["cost"]), to_cents(b.get("salvage") or 0), int(b["useful_life_months"]),
         acc["id"] if acc else None,
         q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='1330'", (cid,))["id"],
         q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='5320'", (cid,))["id"]))
    audit(ctx.conn, ctx.user, "assets.create", "fixed_asset", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("POST", r"/api/assets/depreciation", "assets.manage")
def assets_depreciate(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    start = b.get("period_start")
    end = b.get("period_end")
    if not start or not end:
        today = date.today()
        start = today.replace(day=1).isoformat()
        end = today.isoformat()
    return documents.depreciation_run(ctx.conn, ctx.user, cid, start, end)


# ------------------------------- payroll ----------------------------------
@route("GET", r"/api/employees", "payroll.view")
def employees_list(ctx):
    cid = ensure_company(ctx.user)
    return {"employees": qall(ctx.conn, "SELECT * FROM employees WHERE company_id=? ORDER BY code", (cid,))}


@route("POST", r"/api/employees", "payroll.manage")
def employees_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("code", "name_ar", "name_en", "basic_salary"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    cur = ctx.conn.execute("""INSERT INTO employees (company_id, code, name_ar, name_en, department, job_title,
        hire_date, basic_salary_cents, allowances_cents, deductions_cents, insurance_cents, tax_cents)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b.get("department"), b.get("job_title"),
         b.get("hire_date") or date.today().isoformat(), to_cents(b["basic_salary"]),
         to_cents(b.get("allowances") or 0), to_cents(b.get("deductions") or 0),
         to_cents(b.get("insurance") or 0), to_cents(b.get("tax") or 0)))
    audit(ctx.conn, ctx.user, "employees.create", "employee", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("GET", r"/api/payroll", "payroll.view")
def payroll_list(ctx):
    cid = ensure_company(ctx.user)
    return {"runs": qall(ctx.conn, "SELECT * FROM payroll_runs WHERE company_id=? ORDER BY id DESC", (cid,)),
            "employees": qall(ctx.conn, "SELECT * FROM employees WHERE company_id=? ORDER BY code", (cid,))}


@route("POST", r"/api/payroll/run", "payroll.manage")
def payroll_run(ctx):
    cid = ensure_company(ctx.user)
    period = ctx.body.get("period") or date.today().strftime("%Y-%m")
    return documents.payroll_run(ctx.conn, ctx.user, cid, period)


# ------------------------------- budgets ----------------------------------
@route("GET", r"/api/budgets", "budgets.view")
def budgets_list(ctx):
    cid = ensure_company(ctx.user)
    fy = ctx.qs.get("fiscal_year_id")
    if not fy:
        row = q1(ctx.conn, """SELECT id FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?""",
                 (cid, date.today().isoformat(), date.today().isoformat()))
        fy = row["id"] if row else None
    rows = qall(ctx.conn, """SELECT b.*, a.code, a.name_ar, a.name_en, cc.name_ar AS cost_center
        FROM budgets b JOIN accounts a ON a.id=b.account_id
        LEFT JOIN cost_centers cc ON cc.id=b.cost_center_id
        WHERE b.company_id=? AND b.fiscal_year_id=? ORDER BY a.code, b.period_month""", (cid, fy))
    return {"budgets": rows, "fiscal_year_id": fy,
            "variance": reports.budget_vs_actual(ctx.conn, cid, fy) if fy else []}


@route("POST", r"/api/budgets", "budgets.manage")
def budgets_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    fy = b.get("fiscal_year_id")
    if not fy:
        raise ApiError("MISSING_FIELD: السنة المالية مطلوبة / fiscal_year_id required")
    ctx.conn.execute("""INSERT INTO budgets (company_id, fiscal_year_id, account_id, cost_center_id, period_month,
        budget_cents) VALUES (?,?,?,?,?,?)
        ON CONFLICT(company_id, fiscal_year_id, account_id, cost_center_id, period_month)
        DO UPDATE SET budget_cents=excluded.budget_cents""",
        (cid, int(fy), int(b["account_id"]), b.get("cost_center_id"), int(b["period_month"]),
         to_cents(b["budget"])))
    audit(ctx.conn, ctx.user, "budgets.upsert", "budget", None, b, company_id=cid)
    return {"ok": True}


# ------------------------------- reports ----------------------------------
def _period(qs):
    today = date.today()
    return (qs.get("from") or today.replace(month=1, day=1).isoformat(),
            qs.get("to") or today.isoformat())


@route("GET", r"/api/reports/trial-balance", "reports.view")
def rep_trial_balance(ctx):
    cid = ensure_company(ctx.user)
    d1, d2 = _period(ctx.qs)
    return {"date_from": d1, "date_to": d2, **reports.trial_balance(ctx.conn, cid, d1, d2)}


@route("GET", r"/api/reports/income-statement", "reports.view")
def rep_income(ctx):
    cid = ensure_company(ctx.user)
    d1, d2 = _period(ctx.qs)
    return reports.income_statement(ctx.conn, cid, d1, d2)


@route("GET", r"/api/reports/balance-sheet", "reports.view")
def rep_balance(ctx):
    cid = ensure_company(ctx.user)
    return reports.balance_sheet(ctx.conn, cid, ctx.qs.get("as_of") or date.today().isoformat())


@route("GET", r"/api/reports/cash-flow", "reports.view")
def rep_cash_flow(ctx):
    cid = ensure_company(ctx.user)
    d1, d2 = _period(ctx.qs)
    return reports.cash_flow(ctx.conn, cid, d1, d2)


@route("GET", r"/api/reports/aging", "reports.view")
def rep_aging(ctx):
    cid = ensure_company(ctx.user)
    return reports.aging(ctx.conn, cid, ctx.qs.get("as_of") or date.today().isoformat(),
                         ctx.qs.get("kind", "sales"))


@route("GET", r"/api/reports/vendor-payables", "payables.view")
def rep_payables(ctx):
    cid = ensure_company(ctx.user)
    return reports.vendor_payables(ctx.conn, cid, ctx.qs.get("as_of") or date.today().isoformat())


@route("GET", r"/api/reports/ledger", "reports.view")
def rep_ledger(ctx):
    cid = ensure_company(ctx.user)
    account_id = ctx.qs.get("account_id")
    if not account_id:
        raise ApiError("MISSING_FIELD: الحساب مطلوب / account_id required")
    d1, d2 = _period(ctx.qs)
    data = reports.ledger(ctx.conn, cid, int(account_id), d1, d2)
    if data is None:
        raise ApiError("NOT_FOUND: الحساب غير موجود / Account not found", 404)
    return data


@route("GET", r"/api/reports/budget-variance", "budgets.view")
def rep_budget(ctx):
    cid = ensure_company(ctx.user)
    fy = ctx.qs.get("fiscal_year_id")
    if not fy:
        row = q1(ctx.conn, "SELECT id FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?",
                 (cid, date.today().isoformat(), date.today().isoformat()))
        fy = row["id"] if row else None
    return {"fiscal_year_id": fy, "rows": reports.budget_vs_actual(ctx.conn, cid, fy) if fy else []}


@route("GET", r"/api/reports/export", "reports.export")
def rep_export(ctx):
    """CSV export (UTF-8 BOM so Arabic opens correctly in Excel)."""
    cid = ensure_company(ctx.user)
    kind = ctx.qs.get("kind", "trial-balance")
    d1, d2 = _period(ctx.qs)
    conn = ctx.conn
    if kind == "trial-balance":
        data = reports.trial_balance(conn, cid, d1, d2)
        header = ["Code", "Account (AR)", "Account (EN)", "Opening", "Debit", "Credit", "Closing"]
        rows = [[r["code"], r["name_ar"], r["name_en"], from_cents(r["opening"]),
                 from_cents(r["period_debit"]), from_cents(r["period_credit"]), from_cents(r["closing"])]
                for r in data["rows"]]
    elif kind == "vendor-payables":
        data = reports.vendor_payables(conn, cid, ctx.qs.get("as_of") or d2)
        header = ["Code", "Vendor", "Invoiced", "Paid", "Outstanding", "Overdue", "Next due"]
        rows = [[v["code"], v["name_ar"], from_cents(v["invoiced_cents"]), from_cents(v["paid_cents"]),
                 from_cents(v["outstanding_cents"]), from_cents(v["overdue_cents"]), v["next_due_date"]]
                for v in data["vendors"]]
    elif kind == "aging":
        data = reports.aging(conn, cid, ctx.qs.get("as_of") or d2, ctx.qs.get("type", "purchase"))
        header = ["Invoice", "Party", "Date", "Due date", "Total", "Paid", "Remaining", "Days overdue", "Bucket"]
        rows = [[i["invoice_no"], i["party_ar"], i["invoice_date"], i["due_date"], from_cents(i["total_cents"]),
                 from_cents(i["paid_cents"]), from_cents(i["remaining_cents"]), i["days_overdue"], i["bucket"]]
                for i in data["items"]]
    elif kind == "journal":
        rows_db = conn.execute("""SELECT e.entry_no, e.entry_date, e.memo, e.status, a.code, a.name_ar,
                    l.debit_cents, l.credit_cents FROM journal_entries e
                    JOIN journal_lines l ON l.entry_id=e.id JOIN accounts a ON a.id=l.account_id
                    WHERE e.company_id=? AND e.entry_date BETWEEN ? AND ? ORDER BY e.entry_date, e.id, l.line_no""",
                               (cid, d1, d2)).fetchall()
        header = ["Entry", "Date", "Memo", "Status", "Account code", "Account", "Debit", "Credit"]
        rows = [[r[0], r[1], r[2], r[3], r[4], r[5], from_cents(r[6]), from_cents(r[7])] for r in rows_db]
    elif kind == "income-statement":
        data = reports.income_statement(conn, cid, d1, d2)
        header = ["Section", "Code", "Account", "Amount"]
        rows = [["Revenue", i["code"], i["name_ar"], from_cents(i["amount_cents"])] for i in data["revenue"]]
        rows += [["Expenses", i["code"], i["name_ar"], from_cents(i["amount_cents"])] for i in data["expenses"]]
        rows += [["Net profit", "", "", from_cents(data["net_profit_cents"])]]
    else:
        raise ApiError("UNKNOWN_REPORT: تقرير غير معروف / Unknown report")

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    audit(conn, ctx.user, "reports.export", kind, None, {"from": d1, "to": d2, "rows": len(rows)}, company_id=cid)
    return ctx.file(f"{kind}-{d1}-{d2}.csv", buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8")


# -------------------------- users, audit, notifications -------------------
@route("GET", r"/api/users", "users.manage")
def users_list(ctx):
    cid = ctx.user["company_id"]
    rows = qall(ctx.conn, """SELECT u.id, u.username, u.full_name_ar, u.full_name_en, u.email, u.role_code,
            u.is_active, u.last_login_at, r.name_ar AS role_ar, r.name_en AS role_en
        FROM users u JOIN roles r ON r.code=u.role_code
        WHERE (? IS NULL OR u.company_id=?) ORDER BY u.id""", (cid, cid))
    return {"users": rows, "roles": qall(ctx.conn, "SELECT code, name_ar, name_en, permissions, data_scope FROM roles")}


@route("POST", r"/api/users", "users.manage")
def users_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    for f in ("username", "full_name_ar", "full_name_en", "role_code"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    if q1(ctx.conn, "SELECT 1 FROM users WHERE lower(username)=lower(?)", (b["username"],)):
        raise ApiError("DUPLICATE: اسم المستخدم مستخدم بالفعل / Username already exists")
    role = q1(ctx.conn, "SELECT * FROM roles WHERE code=?", (b["role_code"],))
    if role is None:
        raise ApiError("INVALID_ROLE: الدور غير موجود / Role not found")
    if ctx.user["role_code"] != "company_owner" and role["data_scope"] == "platform":
        raise ApiError("FORBIDDEN: لا يمكن إنشاء مستخدم بصلاحية مدير المنصة / Cannot create platform admin")
    from security import hash_password
    pwd = b.get("password") or ""
    if len(pwd) < 10:
        raise ApiError("WEAK_PASSWORD: كلمة المرور يجب ألا تقل عن 10 أحرف / Password must be at least 10 characters")
    cur = ctx.conn.execute("""INSERT INTO users (company_id, branch_id, username, full_name_ar, full_name_en, email,
        password_hash, role_code, language, theme, password_must_change)
        VALUES (?,?,?,?,?,?,?,?,'ar','light',1)""",
        (cid, ctx.user["branch_id"], b["username"], b["full_name_ar"], b["full_name_en"], b.get("email"),
         hash_password(pwd), role["code"]))
    ctx.conn.execute("INSERT INTO user_company_access (user_id, company_id) VALUES (?,?)", (cur.lastrowid, cid))
    audit(ctx.conn, ctx.user, "users.create", "user", cur.lastrowid,
          {"username": b["username"], "role": role["code"]}, company_id=cid)
    return {"id": cur.lastrowid}


@route("PATCH", r"/api/users/(\d+)", "users.manage")
def users_update(ctx, user_id):
    cid = ensure_company(ctx.user)
    target = q1(ctx.conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if target is None:
        raise ApiError("NOT_FOUND: المستخدم غير موجود / User not found", 404)
    if target["company_id"] != cid and ctx.user["role_code"] != "company_owner":
        raise ApiError("FORBIDDEN: خارج نطاق شركتك / Outside your company scope", 403)
    b = ctx.body
    if b.get("role_code"):
        role = q1(ctx.conn, "SELECT * FROM roles WHERE code=?", (b["role_code"],))
        if role is None:
            raise ApiError("INVALID_ROLE: الدور غير موجود / Role not found")
        if role["data_scope"] == "platform" and ctx.user["role_code"] != "company_owner":
            raise ApiError("FORBIDDEN: غير مصرح بمنح هذا الدور / Not allowed to grant this role")
        ctx.conn.execute("UPDATE users SET role_code=? WHERE id=?", (role["code"], user_id))
    if "is_active" in b:
        if user_id == ctx.user["id"]:
            raise ApiError("FORBIDDEN: لا يمكنك إيقاف حسابك / You cannot disable your own account")
        ctx.conn.execute("UPDATE users SET is_active=? WHERE id=?", (1 if b["is_active"] else 0, user_id))
    audit(ctx.conn, ctx.user, "users.update", "user", user_id, b, company_id=cid)
    return {"ok": True}


@route("GET", r"/api/audit-logs", "audit.view")
def audit_list(ctx):
    cid = ctx.user["company_id"]
    limit = min(500, max(10, int(ctx.qs.get("limit", 200))))
    rows = qall(ctx.conn, """SELECT * FROM audit_logs WHERE (? IS NULL OR company_id=?)
        ORDER BY id DESC LIMIT ?""", (cid, cid, limit))
    return {"logs": rows}


@route("GET", r"/api/notifications")
def notifications_list(ctx):
    cid = ctx.user["company_id"]
    rows = qall(ctx.conn, """SELECT * FROM notifications WHERE company_id=? AND (user_id IS NULL OR user_id=?)
        ORDER BY id DESC LIMIT 50""", (cid, ctx.user["id"]))
    unread = sum(1 for r in rows if not r["is_read"])
    return {"notifications": rows, "unread": unread}


@route("POST", r"/api/notifications/(\d+)/read")
def notification_read(ctx, notif_id):
    ctx.conn.execute("UPDATE notifications SET is_read=1 WHERE id=? AND (user_id IS NULL OR user_id=?)",
                     (notif_id, ctx.user["id"]))
    return {"ok": True}


@route("POST", r"/api/notifications/read-all")
def notifications_read_all(ctx):
    ctx.conn.execute("UPDATE notifications SET is_read=1 WHERE (user_id IS NULL OR user_id=?) AND company_id=?",
                     (ctx.user["id"], ctx.user["company_id"]))
    return {"ok": True}


# --------------------------- fiscal periods -------------------------------
@route("GET", r"/api/fiscal-periods", "reports.view")
def fiscal_periods(ctx):
    cid = ensure_company(ctx.user)
    return {"periods": qall(ctx.conn, """SELECT p.*, fy.name AS fiscal_year, u.full_name_ar AS closed_by_name
        FROM fiscal_periods p JOIN fiscal_years fy ON fy.id=p.fiscal_year_id
        LEFT JOIN users u ON u.id=p.closed_by WHERE p.company_id=? ORDER BY p.start_date DESC""", (cid,))}


@route("POST", r"/api/fiscal-periods/(\d+)/close", "period.close")
def fiscal_close(ctx, period_id):
    cid = ensure_company(ctx.user)
    p = q1(ctx.conn, "SELECT * FROM fiscal_periods WHERE id=? AND company_id=?", (period_id, cid))
    if p is None:
        raise ApiError("NOT_FOUND: الفترة غير موجودة / Period not found", 404)
    if p["status"] == "closed":
        raise ApiError("ALREADY_CLOSED: الفترة مقفلة بالفعل / Period already closed")
    ctx.conn.execute("UPDATE fiscal_periods SET status='closed', closed_by=?, closed_at=? WHERE id=?",
                     (ctx.user["id"], utcnow(), period_id))
    audit(ctx.conn, ctx.user, "period.close", "fiscal_period", period_id,
          {"period_no": p["period_no"]}, company_id=cid)
    return {"ok": True}


@route("POST", r"/api/fiscal-periods/(\d+)/reopen", "period.close")
def fiscal_reopen(ctx, period_id):
    cid = ensure_company(ctx.user)
    p = q1(ctx.conn, "SELECT * FROM fiscal_periods WHERE id=? AND company_id=?", (period_id, cid))
    if p is None:
        raise ApiError("NOT_FOUND: الفترة غير موجودة / Period not found", 404)
    ctx.conn.execute("UPDATE fiscal_periods SET status='open', reopened_by=?, reopened_at=? WHERE id=?",
                     (ctx.user["id"], utcnow(), period_id))
    audit(ctx.conn, ctx.user, "period.reopen", "fiscal_period", period_id,
          {"period_no": p["period_no"]}, company_id=cid)
    return {"ok": True}


# --------------------------- AI assistant ---------------------------------
@route("POST", r"/api/assistant/ask", "ai.use")
def assistant(ctx):
    """Permission-scoped finance assistant.

    This deployment answers with deterministic, auditable analytics computed from
    the ledger (no external LLM call, no raw DB access). Every answer states the
    source report so it can be traced. A pluggable LLM provider can be added later
    behind the same interface.
    """
    cid = ensure_company(ctx.user)
    question = (ctx.body.get("question") or "").strip()
    if not question:
        raise ApiError("EMPTY_QUESTION: اكتب سؤالك / Please type a question")
    conn = ctx.conn
    as_of = date.today().isoformat()
    fy_start = f"{date.today().year}-01-01"
    qn = question.lower()

    def kw(*words):
        return any(w in qn for w in words)

    dashboard_data = reports.dashboard(conn, cid, as_of)
    kpis = dashboard_data["kpis"]
    ap = reports.vendor_payables(conn, cid, as_of)
    inc = reports.income_statement(conn, cid, fy_start, as_of)

    if kw("مورد", "موردين", "vendor", "supplier", "payable", "دائن"):
        top = sorted(ap["vendors"], key=lambda v: -v["outstanding_cents"])[:5]
        lines = "\n".join(f"• {v['name_ar']} — {money_str(v['outstanding_cents'])} ج.م "
                          f"(متأخر: {money_str(v['overdue_cents'])})" for v in top if v["outstanding_cents"])
        answer = (f"إجمالي مديونية الموردين حتى {as_of}: {money_str(ap['totals']['outstanding_cents'])} ج.م، "
                  f"منها {money_str(ap['totals']['overdue_cents'])} ج.م متأخرة.\n"
                  f"أعلى الأرصدة:\n{lines}\n"
                  f"المصدر: تقرير مديونية الموردين (قيود مرحّلة فقط).")
        return {"answer": answer, "source": "vendor_payables",
                "data": {"total_cents": ap["totals"]["outstanding_cents"], "top": top}}
    if kw("سيولة", "نقدية", "cash", "بنك", "liquidity", "خزينة"):
        cash = reports.cash_position(conn, cid)
        details = "\n".join(f"• {a['name_ar']}: {money_str(a['balance_cents'])} ج.م" for a in cash["accounts"])
        answer = (f"السيولة الحالية {money_str(cash['total_cents'])} ج.م موزعة كالتالي:\n{details}\n"
                  f"المتحصلات المتوقعة من العملاء: {money_str(kpis['ar_cents'])} ج.م، "
                  f"والالتزامات المستحقة للموردين: {money_str(kpis['ap_cents'])} ج.م.\n"
                  f"المصدر: أرصدة حسابات البنوك والصناديق من الأستاذ العام.")
        return {"answer": answer, "source": "cash_position", "data": cash}
    if kw("مصروف", "expense", "تكلفة", "cost"):
        exp = sorted(inc["expenses"], key=lambda i: -i["amount_cents"])[:6]
        lines = "\n".join(f"• {e['name_ar']}: {money_str(e['amount_cents'])} ج.م" for e in exp)
        answer = (f"إجمالي المصروفات من بداية السنة حتى {as_of}: {money_str(inc['total_expenses_cents'])} ج.م.\n"
                  f"أكبر البنود:\n{lines}\nالمصدر: قائمة الدخل للفترة.")
        return {"answer": answer, "source": "income_statement", "data": exp}
    if kw("ربح", "أرباح", "profit", "صافي", "إيراد", "مبيعات", "revenue", "sales"):
        answer = (f"من {fy_start} حتى {as_of}: الإيرادات {money_str(inc['total_revenue_cents'])} ج.م، "
                  f"المصروفات {money_str(inc['total_expenses_cents'])} ج.م، "
                  f"صافي الربح {money_str(inc['net_profit_cents'])} ج.م "
                  f"(هامش صافي {round(inc['net_profit_cents']/inc['total_revenue_cents']*100,1) if inc['total_revenue_cents'] else 0}%).\n"
                  f"المصدر: قائمة الدخل — أرقام فعلية من القيود المرحّلة، وليست توقعات.")
        return {"answer": answer, "source": "income_statement", "data": inc}
    if kw("متأخر", "overdue", "تحصيل", "collection", "عملاء", "عميل", "customer"):
        ar = reports.aging(conn, cid, as_of, "sales")
        answer = (f"أرصدة العملاء المستحقة {money_str(kpis['ar_cents'])} ج.م، "
                  f"منها {money_str(kpis['overdue_ar_cents'])} ج.م متأخرة على {kpis['overdue_count']} فاتورة.\n"
                  f"أعمار الديون: جاري {money_str(ar['buckets']['current'])}، "
                  f"1–30 يوم {money_str(ar['buckets']['1_30'])}، 31–60 يوم {money_str(ar['buckets']['31_60'])}، "
                  f"61–90 يوم {money_str(ar['buckets']['61_90'])}، أكثر من 90 يوم {money_str(ar['buckets']['over_90'])}.\n"
                  f"المصدر: تقرير أعمار الديون المدينة.")
        return {"answer": answer, "source": "aging", "data": ar}
    if kw("موازنة", "budget", "انحراف", "variance"):
        fy = q1(conn, "SELECT id FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?",
                (cid, as_of, as_of))
        rows = reports.budget_vs_actual(conn, cid, fy["id"]) if fy else []
        worst = sorted([r for r in rows if r["variance_cents"] < 0], key=lambda r: r["variance_cents"])[:4]
        lines = "\n".join(f"• {r['name_ar']}: موازنة {money_str(r['budget_cents'])} / فعلي "
                          f"{money_str(r['actual_cents'])} (انحراف {money_str(r['variance_cents'])})" for r in worst)
        answer = (f"أكبر انحرافات تجاوز الموازنة:\n{lines or 'لا توجد تجاوزات مسجلة.'}\n"
                  f"المصدر: تقرير الموازنة مقابل الفعلي للسنة {date.today().year}.")
        return {"answer": answer, "source": "budget_variance", "data": worst}
    if kw("ملخص", "summary", "لوحة", "dashboard", "أداء"):
        answer = (f"ملخص الأداء حتى {as_of}:\n"
                  f"• الإيرادات: {money_str(kpis['revenue_cents'])} ج.م\n"
                  f"• المصروفات: {money_str(kpis['expenses_cents'])} ج.م\n"
                  f"• صافي الربح: {money_str(kpis['net_profit_cents'])} ج.م\n"
                  f"• السيولة: {money_str(kpis['cash_cents'])} ج.م\n"
                  f"• مديونية الموردين: {money_str(kpis['ap_cents'])} ج.م\n"
                  f"• مستحقات العملاء: {money_str(kpis['ar_cents'])} ج.م\n"
                  f"• اعتمادات معلّقة: {kpis['pending_approvals']}\n"
                  f"المصدر: لوحة التحكم — بيانات فعلية من القيود المرحّلة.")
        return {"answer": answer, "source": "dashboard", "data": kpis}
    answer = ("أستطيع الإجابة عن أسئلة تخص: المبيعات والأرباح، المصروفات، السيولة والبنوك، "
              "مديونية الموردين وأعمار الديون، الموازنة مقابل الفعلي، أو ملخص الأداء.\n"
              "لم أفهم السؤال — جرّب مثلًا: «ما إجمالي مديونية الموردين؟» أو «ما صافي الربح هذا العام؟».\n"
              "تنبيه: كل الأرقام مأخوذة من القيود المرحّلة وفق صلاحياتك، ولا يقوم المساعد بأي ترحيل أو دفع.")
    return {"answer": answer, "source": "help", "data": {}}


# ==========================================================================
# request handling
# ==========================================================================
class Ctx:
    def __init__(self, handler, method, path, qs, body, token):
        self.handler = handler
        self.method = method
        self.path = path
        self.qs = qs
        self.body = body
        self.token_hash = token
        self.ip = handler.client_address[0]
        self.headers = handler.headers
        self.conn = None
        self.user = None

    def file(self, filename, content, content_type):
        return {"__file__": True, "filename": filename, "content": content, "content_type": content_type}


class Handler(BaseHTTPRequestHandler):
    server_version = "EltaranisiERP/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------------- helpers ----------------
    def _send(self, status, payload, content_type="application/json; charset=utf-8", extra_headers=None):
        if isinstance(payload, (dict, list)):
            payload = j(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, status, obj):
        self._send(status, j(obj))

    def _error(self, status, message, details=None):
        self._json(status, {"error": message, "details": details or {}})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("PAYLOAD_TOO_LARGE: حجم الطلب كبير جدًا / Payload too large", 413)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError("INVALID_JSON: صيغة الطلب غير صحيحة / Invalid JSON body", 400)

    def _auth(self, conn):
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return None, None
        raw = header[7:].strip()
        hashed = token_hash(raw)
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (hashed,)).fetchone()
        if row is not None:
            if row["revoked"] or row["expires_at"] < utcnow():
                return None, hashed          # explicit logout / expiry wins
            return load_user(conn, row["user_id"]), hashed
        # Unknown session row. On an ephemeral (serverless) deployment the row may
        # simply live in another instance's database, so a valid signed token is
        # accepted; on a durable deployment an unknown token stays rejected.
        if EPHEMERAL_AUTH:
            claims = verify_token(raw)
            if claims:
                user = load_user(conn, claims["user_id"])
                if user and user["is_active"]:
                    return user, hashed
        return None, hashed

    # ---------------- verbs ----------------
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_HEAD(self):
        self._handle("GET")

    def _handle(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._handle_api(method, path, urllib.parse.parse_qs(parsed.query))
        return self._serve_static(path)

    # ---------------- API ----------------
    def _handle_api(self, method, path, qs_raw):
        qs = {k: v[0] for k, v in qs_raw.items()}
        try:
            body = self._read_body() if method in ("POST", "PATCH", "PUT", "DELETE") else {}
        except ApiError as exc:
            return self._error(exc.status, exc.message)

        with transaction() as conn:
            ctx = Ctx(self, method, path, qs, body, None)
            ctx.conn = conn
            try:
                user, token = self._auth(conn)
                ctx.user, ctx.token_hash = user, token
                matched = None
                for m, rx, perm, fn in ROUTES:
                    if m == method and rx.match(path):
                        matched = (rx, perm, fn)
                        break
                if matched is None:
                    raise ApiError("NOT_FOUND: المسار غير موجود / Route not found", 404)
                rx, perm, fn = matched
                if fn not in (login, health, dev_accounts):
                    if user is None:
                        raise AuthError("UNAUTHENTICATED: يلزم تسجيل الدخول / Authentication required", 401)
                    if perm:
                        require(user, perm)
                args = rx.match(path).groups()
                result = fn(ctx, *args)
                if isinstance(result, dict) and result.get("__file__"):
                    disp = "inline" if result.get("inline") else "attachment"
                    self._send(200, result["content"], result["content_type"],
                               {"Content-Disposition": f'{disp}; filename="{result["filename"]}"',
                                "Cache-Control": "no-store"})
                    return
                self._json(200, result)
            except (ApiError,) as exc:
                self._error(exc.status, exc.message, exc.details)
            except accounting.ValidationError as exc:
                self._error(exc.status, exc.message, exc.details)
            except PermissionError_ as exc:
                self._error(403, exc.message)
            except AuthError as exc:
                self._error(exc.status, exc.message)
            except ValueError as exc:
                self._error(400, f"INVALID_INPUT: {exc}")
            except Exception as exc:
                traceback.print_exc()
                self._error(500, "INTERNAL_ERROR: حدث خطأ غير متوقع / Unexpected server error",
                            {"type": type(exc).__name__})

    # ---------------- static ----------------
    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        content = target.read_bytes()
        self._send(200, content, ctype, {"Cache-Control": "no-cache"})


def main():
    if not (BASE_DIR / "data" / "erp.db").exists():
        print("Database not found — run: python3 seed.py")
    try:
        from db import migrate, transaction
        migrate()
        with transaction() as conn:
            changed = sync_system_roles(conn)     # roles are code-defined, DB-refreshed
        if changed:
            print(f"roles synced: {changed}")
    except Exception as exc:      # never block startup on an additive migration
        print("migration warning:", exc)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"ELTARANISI ERP running on http://0.0.0.0:{PORT}")
    server.serve_forever()




# ==========================================================================
# Document exports — tax invoice PDF / Excel / e-invoicing readiness JSON
# ==========================================================================
import hashlib  # noqa: E402
import exporter  # noqa: E402


def _record_einvoice(conn, user, company_id, kind, invoice_id):
    """Create (once) and return the immutable e-invoice readiness record."""
    existing = q1(conn, "SELECT * FROM einvoice_documents WHERE invoice_kind=? AND invoice_id=?",
                  (kind, invoice_id))
    if existing:
        return dict(existing)
    payload = exporter.einvoice_json(conn, company_id, kind, invoice_id)
    conn.execute("""INSERT OR IGNORE INTO einvoice_documents
        (company_id, invoice_kind, invoice_id, document_uuid, digest, payload, issued_by)
        VALUES (?,?,?,?,?,?,?)""",
                 (company_id, kind, invoice_id, payload["documentUuid"], payload["digest"],
                  json.dumps(payload, ensure_ascii=False), user["id"]))
    audit(conn, user, "einvoice.issue", f"{kind}_invoice", invoice_id,
          {"uuid": payload["documentUuid"], "digest": payload["digest"][:24] + "…"}, company_id=company_id)
    return q1(conn, "SELECT * FROM einvoice_documents WHERE invoice_kind=? AND invoice_id=?",
              (kind, invoice_id))


def _lang(ctx):
    lang = (ctx.qs.get("lang") or ctx.qs.get("language") or "ar").lower()
    return "en" if lang.startswith("en") else "ar"


def _disposition(name, inline=False):
    return {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"; filename*=UTF-8\'\'{name}'}


@route("GET", r"/api/sales-invoices/(\d+)/pdf", "sales.view")
def sales_pdf(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    data = exporter.invoice_pdf(ctx.conn, cid, "sales", int(invoice_id), lang)
    inv = q1(ctx.conn, "SELECT invoice_no FROM sales_invoices WHERE id=? AND company_id=?",
             (int(invoice_id), cid))
    if inv is None:
        raise ApiError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", 404)
    rec = _record_einvoice(ctx.conn, ctx.user, cid, "sales", int(invoice_id))
    audit(ctx.conn, ctx.user, "sales_invoice.pdf", "sales_invoice", int(invoice_id),
          {"lang": lang, "uuid": rec["document_uuid"][:16] + "…"}, company_id=cid)
    name = f"{inv['invoice_no']}-{lang}.pdf"
    return {"__file__": True, "content": data, "content_type": "application/pdf",
            "filename": name, "inline": True}


@route("GET", r"/api/purchase-invoices/(\d+)/pdf", "purchases.view")
def purchase_pdf(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    inv = q1(ctx.conn, "SELECT invoice_no FROM purchase_invoices WHERE id=? AND company_id=?",
             (int(invoice_id), cid))
    if inv is None:
        raise ApiError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", 404)
    lang = _lang(ctx)
    data = exporter.invoice_pdf(ctx.conn, cid, "purchase", int(invoice_id), lang)
    rec = _record_einvoice(ctx.conn, ctx.user, cid, "purchase", int(invoice_id))
    audit(ctx.conn, ctx.user, "purchase_invoice.pdf", "purchase_invoice", int(invoice_id),
          {"lang": lang, "uuid": rec["document_uuid"][:16] + "…"}, company_id=cid)
    return {"__file__": True, "content": data, "content_type": "application/pdf",
            "filename": f"{inv['invoice_no']}-{lang}.pdf", "inline": True}


@route("GET", r"/api/sales-invoices/(\d+)/xlsx", "sales.view")
def sales_xlsx(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    inv = q1(ctx.conn, "SELECT invoice_no FROM sales_invoices WHERE id=? AND company_id=?",
             (int(invoice_id), cid))
    if inv is None:
        raise ApiError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", 404)
    data = exporter.invoice_xlsx(ctx.conn, cid, "sales", int(invoice_id), _lang(ctx))
    audit(ctx.conn, ctx.user, "sales_invoice.xlsx", "sales_invoice", int(invoice_id), {}, company_id=cid)
    return {"__file__": True, "content": data,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"{inv['invoice_no']}.xlsx"}


@route("GET", r"/api/purchase-invoices/(\d+)/xlsx", "purchases.view")
def purchase_xlsx(ctx, invoice_id):
    cid = ensure_company(ctx.user)
    inv = q1(ctx.conn, "SELECT invoice_no FROM purchase_invoices WHERE id=? AND company_id=?",
             (int(invoice_id), cid))
    if inv is None:
        raise ApiError("NOT_FOUND: الفاتورة غير موجودة / Invoice not found", 404)
    data = exporter.invoice_xlsx(ctx.conn, cid, "purchase", int(invoice_id), _lang(ctx))
    audit(ctx.conn, ctx.user, "purchase_invoice.xlsx", "purchase_invoice", int(invoice_id), {}, company_id=cid)
    return {"__file__": True, "content": data,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"{inv['invoice_no']}.xlsx"}


@route("GET", r"/api/(sales|purchase)-invoices/(\d+)/einvoice", "sales.view")
def invoice_einvoice(ctx, kind, invoice_id):
    cid = ensure_company(ctx.user)
    k = "sales" if kind == "sales" else "purchase"
    rec = _record_einvoice(ctx.conn, ctx.user, cid, k, int(invoice_id))
    payload = json.loads(rec["payload"])
    payload["stored"] = {"uuid": rec["document_uuid"], "digest": rec["digest"],
                         "created_at": rec["created_at"]}
    return payload


@route("GET", r"/api/reports/pdf", "reports.view")
def report_pdf_route(ctx):
    """PDF export for the main statements (Arabic or English)."""
    cid = ensure_company(ctx.user)
    company = exporter.company_block(ctx.conn, cid)
    kind = ctx.qs.get("kind", "trial-balance")
    lang = _lang(ctx)
    d1, d2 = _period(ctx.qs)
    ar = lang != "en"
    cur = company.get("base_currency") or "EGP"

    if kind == "trial-balance":
        data = reports.trial_balance(ctx.conn, cid, d1, d2)
        cols = [{"ar": "الكود", "en": "Code", "w": 0.9, "align": "center", "rtl": False},
                {"ar": "الحساب", "en": "Account", "w": 3.4},
                {"ar": "رصيد افتتاحي", "en": "Opening", "w": 1.5, "align": "center", "rtl": False},
                {"ar": "مدين", "en": "Debit", "w": 1.5, "align": "center", "rtl": False},
                {"ar": "دائن", "en": "Credit", "w": 1.5, "align": "center", "rtl": False},
                {"ar": "رصيد ختامي", "en": "Closing", "w": 1.5, "align": "center", "rtl": False}]
        rows = [[r["code"], (r["name_ar"] if ar else r["name_en"]), exporter.money(r["opening"]),
                 exporter.money(r["period_debit"]), exporter.money(r["period_credit"]),
                 exporter.money(r["closing"])] for r in data["rows"]]
        title_ar, title_en = "ميزان المراجعة", "Trial balance"
        notes = [("الإجمالي: مدين %s / دائن %s — متوازن ✅" if ar else "Totals: debit %s / credit %s — balanced")
                 % (exporter.money(data["totals"]["period_debit"]), exporter.money(data["totals"]["period_credit"]))]
    elif kind == "income-statement":
        data = reports.income_statement(ctx.conn, cid, d1, d2)
        cols = [{"ar": "الكود", "en": "Code", "w": 0.9, "align": "center", "rtl": False},
                {"ar": "البند", "en": "Item", "w": 3.8},
                {"ar": "المبلغ", "en": "Amount", "w": 1.6, "align": "center", "rtl": False}]
        rows = [[("" if ar else "Revenue"), "", ""]]
        rows = [[i["code"], (i["name_ar"] if ar else i["name_en"]), exporter.money(i["amount_cents"])]
                for i in data["revenue"]]
        rows.append(["", ("إجمالي الإيرادات" if ar else "Total revenue"), exporter.money(data["total_revenue_cents"])])
        rows.append(["", ("المصروفات" if ar else "Expenses"), ""])
        rows += [[i["code"], (i["name_ar"] if ar else i["name_en"]), exporter.money(i["amount_cents"])]
                 for i in data["expenses"]]
        rows.append(["", ("إجمالي المصروفات" if ar else "Total expenses"), exporter.money(data["total_expenses_cents"])])
        rows.append(["", ("صافي الربح" if ar else "Net profit"), exporter.money(data["net_profit_cents"])])
        title_ar, title_en = "قائمة الدخل", "Income statement"
        notes = [("صافي الربح: %s %s" % (exporter.money(data["net_profit_cents"]), cur))]
    elif kind == "balance-sheet":
        data = reports.balance_sheet(ctx.conn, cid, ctx.qs.get("as_of") or d2)
        cols = [{"ar": "الكود", "en": "Code", "w": 0.9, "align": "center", "rtl": False},
                {"ar": "البند", "en": "Item", "w": 3.8},
                {"ar": "الرصيد", "en": "Balance", "w": 1.6, "align": "center", "rtl": False}]
        rows = []
        for section, label_ar, label_en in (("assets", "الأصول", "Assets"),
                                            ("liabilities", "الالتزامات", "Liabilities"),
                                            ("equity", "حقوق الملكية", "Equity")):
            rows.append(["", (label_ar if ar else label_en), ""])
            for it in data.get(section, []):
                rows.append([it.get("code", ""), (it.get("name_ar") if ar else it.get("name_en")) or "",
                             exporter.money(it.get("amount_cents", it.get("balance_cents", 0)))])
        title_ar, title_en = "المركز المالي", "Balance sheet"
        notes = [("متوازن ✅" if data.get("balanced") else "غير متوازن ⚠")
                 if ar else ("Balanced" if data.get("balanced") else "NOT balanced")]
    elif kind == "vendor-payables":
        data = reports.vendor_payables(ctx.conn, cid, ctx.qs.get("as_of") or d2)
        cols = [{"ar": "الكود", "en": "Code", "w": 0.9, "align": "center", "rtl": False},
                {"ar": "المورد", "en": "Vendor", "w": 3.0},
                {"ar": "إجمالي الفواتير", "en": "Invoiced", "w": 1.4, "align": "center", "rtl": False},
                {"ar": "المدفوع", "en": "Paid", "w": 1.3, "align": "center", "rtl": False},
                {"ar": "المتبقي", "en": "Outstanding", "w": 1.4, "align": "center", "rtl": False},
                {"ar": "متأخر", "en": "Overdue", "w": 1.3, "align": "center", "rtl": False},
                {"ar": "أقرب استحقاق", "en": "Next due", "w": 1.2, "align": "center", "rtl": False}]
        rows = [[v["code"], (v["name_ar"] if ar else v["name_en"]), exporter.money(v["invoiced_cents"]),
                 exporter.money(v["paid_cents"]), exporter.money(v["outstanding_cents"]),
                 exporter.money(v["overdue_cents"]), v.get("next_due_date") or "—"] for v in data["vendors"]]
        t = data["totals"]
        title_ar, title_en = "مديونية الموردين", "Vendor payables"
        notes = [("الإجمالي %s · المتبقي %s · المتأخر %s · رصيد الأستاذ %s"
                  % (exporter.money(t["invoiced_cents"]), exporter.money(t["outstanding_cents"]),
                     exporter.money(t["overdue_cents"]), exporter.money(t["journal_balance_cents"])))
                 if ar else
                 ("Total %s · Outstanding %s · Overdue %s · GL %s"
                  % (exporter.money(t["invoiced_cents"]), exporter.money(t["outstanding_cents"]),
                     exporter.money(t["overdue_cents"]), exporter.money(t["journal_balance_cents"])))]
    elif kind == "aging":
        data = reports.aging(ctx.conn, cid, ctx.qs.get("as_of") or d2, ctx.qs.get("type", "purchase"))
        cols = [{"ar": "الفاتورة", "en": "Invoice", "w": 1.3, "align": "center", "rtl": False},
                {"ar": "الطرف", "en": "Party", "w": 2.6},
                {"ar": "التاريخ", "en": "Date", "w": 1.1, "align": "center", "rtl": False},
                {"ar": "الاستحقاق", "en": "Due", "w": 1.1, "align": "center", "rtl": False},
                {"ar": "الإجمالي", "en": "Total", "w": 1.3, "align": "center", "rtl": False},
                {"ar": "المتبقي", "en": "Remaining", "w": 1.3, "align": "center", "rtl": False},
                {"ar": "تأخير (يوم)", "en": "Days late", "w": 0.9, "align": "center", "rtl": False}]
        rows = [[i["invoice_no"], i["party_ar"], i["invoice_date"], i["due_date"],
                 exporter.money(i["total_cents"]), exporter.money(i["remaining_cents"]),
                 i["days_overdue"]] for i in data["items"]]
        title_ar, title_en = "أعمار الديون", "Aging report"
        notes = []
    else:
        raise ApiError("INVALID_INPUT: نوع التقرير غير مدعوم / Unsupported report kind", 400)

    subtitle_ar = f"الفترة {d1} إلى {d2} · {company.get('name_ar', '')}"
    subtitle_en = f"Period {d1} to {d2} · {company.get('name_en', '')}"
    pdf_bytes = exporter.report_pdf(title_ar, title_en, cols, rows, company, lang,
                                    subtitle_ar, subtitle_en, notes)
    label = {"trial-balance": "trial-balance", "income-statement": "income-statement",
             "balance-sheet": "balance-sheet", "vendor-payables": "vendor-payables",
             "aging": "aging"}.get(kind, kind)
    audit(ctx.conn, ctx.user, "reports.pdf", "report", None, {"kind": kind, "lang": lang}, company_id=cid)
    return {"__file__": True, "content": pdf_bytes, "content_type": "application/pdf",
            "filename": f"{label}-{d1}_{d2}-{lang}.pdf", "inline": True}


@route("GET", r"/api/reports/xlsx", "reports.export")
def report_xlsx_route(ctx):
    """Excel export for the main statements (RTL sheet, real number formats)."""
    cid = ensure_company(ctx.user)
    kind = ctx.qs.get("kind", "trial-balance")
    d1, d2 = _period(ctx.qs)
    ar = _lang(ctx) != "en"
    T = (lambda a, e: a) if ar else (lambda a, e: e)
    if kind == "trial-balance":
        data = reports.trial_balance(ctx.conn, cid, d1, d2)
        cols = [T("الكود", "Code"), T("الحساب", "Account"), T("رصيد افتتاحي", "Opening"),
                T("مدين", "Debit"), T("دائن", "Credit"), T("رصيد ختامي", "Closing")]
        rows = [[r["code"], (r["name_ar"] if ar else r["name_en"]), r["opening"] / 100,
                 r["period_debit"] / 100, r["period_credit"] / 100, r["closing"] / 100]
                for r in data["rows"]]
        money_cols = (2, 3, 4, 5)
        title = T("ميزان المراجعة", "Trial balance")
    elif kind == "vendor-payables":
        data = reports.vendor_payables(ctx.conn, cid, ctx.qs.get("as_of") or d2)
        cols = [T("الكود", "Code"), T("المورد", "Vendor"), T("إجمالي الفواتير", "Invoiced"),
                T("المدفوع", "Paid"), T("المتبقي", "Outstanding"), T("المتأخر", "Overdue"),
                T("أقرب استحقاق", "Next due")]
        rows = [[v["code"], (v["name_ar"] if ar else v["name_en"]), v["invoiced_cents"] / 100,
                 v["paid_cents"] / 100, v["outstanding_cents"] / 100, v["overdue_cents"] / 100,
                 v.get("next_due_date") or "—"] for v in data["vendors"]]
        money_cols = (2, 3, 4, 5)
        title = T("مديونية الموردين", "Vendor payables")
    elif kind == "aging":
        data = reports.aging(ctx.conn, cid, ctx.qs.get("as_of") or d2, ctx.qs.get("type", "purchase"))
        cols = [T("الفاتورة", "Invoice"), T("الطرف", "Party"), T("التاريخ", "Date"),
                T("الاستحقاق", "Due"), T("الإجمالي", "Total"), T("المدفوع", "Paid"),
                T("المتبقي", "Remaining"), T("تأخير", "Days late")]
        rows = [[i["invoice_no"], i["party_ar"], i["invoice_date"], i["due_date"],
                 i["total_cents"] / 100, i["paid_cents"] / 100, i["remaining_cents"] / 100,
                 i["days_overdue"]] for i in data["items"]]
        money_cols = (4, 5, 6)
        title = T("أعمار الديون", "Aging")
    else:
        raise ApiError("INVALID_INPUT: نوع التقرير غير مدعوم / Unsupported report kind", 400)
    content = exporter.report_xlsx(f"{title} {d1} — {d2}", cols, rows, rtl=ar, money_cols=money_cols)
    audit(ctx.conn, ctx.user, "reports.xlsx", "report", None, {"kind": kind}, company_id=cid)
    return {"__file__": True, "content": content,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"{kind}-{d1}_{d2}.xlsx"}


if __name__ == "__main__":
    main()
