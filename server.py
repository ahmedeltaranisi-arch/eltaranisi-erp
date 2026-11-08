"""HTTP API + static file server for ELTARANISI ERP (Python stdlib only).

Run:  python3 server.py  →  http://0.0.0.0:8000
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
import mimetypes
import os
import re
import traceback
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import accounting
import allowance
import debt
import deletion
import documents
import editing
import reports
import db
import d1 as _d1_client
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
def _pick_backup_dir() -> Path:
    """A writable place for database snapshots (serverless hosts are read-only
    except /tmp, so the directory is probed instead of assumed)."""
    for candidate in (os.environ.get("BACKUP_DIR"), BASE_DIR / "backups", Path("/tmp/eltaranisi-backups")):
        if not candidate:
            continue
        try:
            path = Path(candidate)
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            continue
    return Path("/tmp")


BACKUP_DIR = _pick_backup_dir()
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


# ==========================================================================
# response cache
# ==========================================================================
# The production database is Cloudflare D1, reached over HTTPS: every SQL
# statement is a network round-trip (~120-170 ms). A screen that issues 33 of
# them took ~6 s even though the SQL itself is trivial. Reference data (chart of
# accounts, banks, customers, vendors, products, pick-lists) and whole read-only
# screens therefore keep a short-lived copy in the function's memory.
#
# Correctness: any successful write on any instance bumps the epoch, which
# invalidates every cached entry (writes are far rarer than reads). The TTL is a
# belt-and-braces bound for instances that did not see the write.
CACHE_TTL = int(os.environ.get("ERP_CACHE_TTL", "60"))
CACHE_MAX_ENTRIES = 256
CACHEABLE_GET = {
    "/api/meta", "/api/dashboard", "/api/accounts", "/api/banks", "/api/customers",
    "/api/vendors", "/api/products", "/api/debt/meta", "/api/debt/accounts",
    "/api/debt/summary", "/api/payments/bootstrap", "/api/journal-entries",
    "/api/sales-invoices", "/api/purchase-invoices", "/api/reports/trial-balance",
    "/api/payments",
}
AUTH_TTL = int(os.environ.get("ERP_AUTH_CACHE_TTL", "60"))
_auth_cache = {}            # token hash -> (expires_at, user)
_cache_lock = threading.Lock()
_cache = {}                 # key -> (expires_at, serialized body)
_cache_epoch = 0            # bumped on every write
_cache_stats = {"hits": 0, "misses": 0}


def cache_key(path, company_id, qs_raw):
    return (path, company_id, tuple(sorted(qs_raw.items())), _cache_epoch)


def cache_get(key):
    if CACHE_TTL <= 0:
        return None
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > time.monotonic():
            _cache_stats["hits"] += 1
            return entry[1]
        if entry:
            _cache.pop(key, None)
        _cache_stats["misses"] += 1
        return None


def cache_put(key, payload):
    if CACHE_TTL <= 0:
        return
    with _cache_lock:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            # drop expired entries first, then the oldest one
            now = time.monotonic()
            for k in [k for k, v in _cache.items() if v[0] <= now]:
                _cache.pop(k, None)
            if len(_cache) >= CACHE_MAX_ENTRIES:
                _cache.pop(next(iter(_cache)), None)
        _cache[key] = (time.monotonic() + CACHE_TTL, payload)


def cache_clear():
    global _cache_epoch
    with _cache_lock:
        _cache_epoch += 1
        _cache.clear()
        _auth_cache.clear()      # a write may also be a logout / role change


def auth_cache_get(user_id):
    """Cached user row (roles + permissions) — never the session itself."""
    if AUTH_TTL <= 0 or user_id is None:
        return None
    with _cache_lock:
        entry = _auth_cache.get(user_id)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        _auth_cache.pop(user_id, None)
        return None


def auth_cache_put(user_id, user):
    if AUTH_TTL <= 0 or user_id is None or user is None:
        return
    with _cache_lock:
        if len(_auth_cache) > 512:
            _auth_cache.clear()
        _auth_cache[user_id] = (time.monotonic() + AUTH_TTL, user)


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


# ------------------------------ sign-in protection ------------------------
# A login endpoint is the single most attacked screen of an accounting system.
# Three independent brakes are applied, cheapest first:
#
#   1. per-IP failure budget   — no single client can grind passwords
#   2. per-username lockout    — one account cannot be brute-forced from a botnet
#   3. constant-time answers   — an unknown username costs the same wall time as
#                                a wrong password (no user enumeration)
#
# State lives in-process (this deployment is a single server / single lambda
# container). Persisted counters still work: ``users.failed_attempts`` and
# ``users.locked_until`` are updated in the database as before.
LOGIN_WINDOW_SECONDS = int(os.environ.get("ERP_LOGIN_WINDOW", "900"))       # 15 minutes
LOGIN_MAX_ATTEMPTS_IP = int(os.environ.get("ERP_LOGIN_MAX_IP", "12"))
LOGIN_MAX_ATTEMPTS_USER = int(os.environ.get("ERP_LOGIN_MAX_USER", "8"))
_login_failures = {}          # key -> [timestamps]
_login_lock = threading.Lock()

# a fixed, deliberately slow hash used when the username does not exist, so the
# response time never reveals whether an account is registered
_DUMMY_HASH = ("pbkdf2_sha256$240000$00112233445566778899aabbccddeeff$"
               "4f8b2c1d9e6a7b3c5d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c")


def _rate_key(ctx, username):
    return f"{ctx.ip}|{username}"


def _rate_blocked(key):
    now = time.monotonic()
    with _login_lock:
        hits = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[key] = hits
        return len(hits)


def _rate_hit(key):
    now = time.monotonic()
    with _login_lock:
        hits = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        hits.append(now)
        _login_failures[key] = hits
        if len(_login_failures) > 2048:          # keep the map bounded
            for k in list(_login_failures)[:512]:
                _login_failures.pop(k, None)


def _rate_reset(key):
    with _login_lock:
        _login_failures.pop(key, None)


@route("POST", r"/api/auth/login")
def login(ctx):
    body = ctx.body
    username = (body.get("username") or "").strip().lower()[:80]
    password = body.get("password") or ""
    conn = ctx.conn

    if not username or not password:
        raise ApiError("MISSING_FIELDS: أدخل اسم المستخدم وكلمة المرور / Enter username and password", 400)

    ip_hits = _rate_blocked(f"ip|{ctx.ip}")
    if ip_hits >= LOGIN_MAX_ATTEMPTS_IP:
        raise ApiError("TOO_MANY_ATTEMPTS: محاولات دخول كثيرة من هذا الجهاز. حاول بعد قليل / "
                       "Too many attempts from this device — try again later", 429)
    key = _rate_key(ctx, username)
    if _rate_blocked(key) >= LOGIN_MAX_ATTEMPTS_USER:
        raise ApiError("TOO_MANY_ATTEMPTS: محاولات دخول كثيرة لهذا الحساب. حاول بعد قليل / "
                       "Too many attempts for this account — try again later", 429)

    row = q1(conn, "SELECT * FROM users WHERE lower(username)=?", (username,))
    # Always run one PBKDF2 verification, even for a missing user: the timing of
    # the answer must not tell an attacker whether the account exists.
    stored = row["password_hash"] if row is not None else _DUMMY_HASH
    password_ok = verify_password(password, stored)
    if row is None or not password_ok:
        _rate_hit(key)
        _rate_hit(f"ip|{ctx.ip}")
        if row is not None:
            attempts = (row["failed_attempts"] or 0) + 1
            locked_until = None
            if attempts >= LOGIN_MAX_ATTEMPTS_USER:
                locked_until = (datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
                attempts = 0
            conn.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                         (attempts, locked_until, row["id"]))
            if locked_until:
                audit(conn, None, "auth.locked", "user", row["id"],
                      {"reason": "too many failed attempts", "ip": ctx.ip})
        remaining = max(0, LOGIN_MAX_ATTEMPTS_IP - ip_hits - 1)
        raise ApiError("INVALID_CREDENTIALS: بيانات الدخول غير صحيحة / Invalid username or password",
                       401, {"attempts_left": remaining})
    if not row["is_active"]:
        raise ApiError("INACTIVE: الحساب موقوف / Account disabled", 403)
    if row["locked_until"] and row["locked_until"] > utcnow():
        raise ApiError("LOCKED: الحساب مقفل مؤقتًا بسبب محاولات دخول متكررة / Account temporarily locked", 423)

    _rate_reset(key)
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


WEAK_PASSWORDS = {"password", "12345678", "123456789", "1234567890", "qwerty123", "admin123",
                  "welcome123", "eltaranisi", "changeme", "letmein123", "password1"}


def password_problems(password: str, username: str = "", full_name: str = "") -> list:
    """Password policy shared by every place a password is set.

    The rules are deliberately simple and explainable (a finance team, not a
    security team, has to live with them), but they do block the classic
    "ahmed.eltaranisi = the company name" case the customer complained about.
    """
    problems = []
    if len(password) < 10:
        problems.append("10 أحرف على الأقل / at least 10 characters")
    if not any(c.isdigit() for c in password):
        problems.append("رقم واحد على الأقل / at least one digit")
    if not any(c.isalpha() for c in password):
        problems.append("حرف واحد على الأقل / at least one letter")
    low = password.lower()
    if len(set(low)) < 4:
        problems.append("تنويع الأحرف / too repetitive")
    tokens = [t for t in re.split(r"[^a-z0-9]+", (username or "").lower()) if len(t) >= 3]
    tokens += [t for t in re.split(r"[^a-z0-9]+", (full_name or "").lower()) if len(t) >= 4]
    for token in tokens:
        if token and token in low:
            problems.append("لا تحتوي على اسم المستخدم أو اسمك / must not contain your username or name")
            break
    if low in WEAK_PASSWORDS or any(low.startswith(w) and len(low) <= len(w) + 2 for w in WEAK_PASSWORDS):
        problems.append("كلمة مرور شائعة / a common password")
    return problems


@route("POST", r"/api/auth/change-password")
def change_password(ctx):
    old = ctx.body.get("current_password") or ""
    new = ctx.body.get("new_password") or ""
    row = q1(ctx.conn, "SELECT password_hash, username, full_name_en, full_name_ar FROM users WHERE id=?",
             (ctx.user["id"],))
    problems = password_problems(new, row["username"], f'{row["full_name_en"]} {row["full_name_ar"]}')
    if problems:
        raise ApiError("WEAK_PASSWORD: كلمة المرور ضعيفة / Password is too weak",
                       400, {"rules": problems})
    if verify_password(old, row["password_hash"]):
        pass
    else:
        raise ApiError("INVALID_CREDENTIALS: كلمة المرور الحالية غير صحيحة / Current password incorrect", 401)
    if old == new:
        raise ApiError("SAME_PASSWORD: الكلمة الجديدة مطابقة للحالية / New password must differ")
    from security import hash_password
    ctx.conn.execute("UPDATE users SET password_hash=?, password_must_change=0 WHERE id=?",
                     (hash_password(new), ctx.user["id"]))
    # every other session of this user is revoked: a password change is how you
    # kick out a shared or stolen session
    ctx.conn.execute("UPDATE sessions SET revoked=1 WHERE user_id=? AND token<>?",
                     (ctx.user["id"], ctx.token_hash))
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


def demo_accounts_enabled() -> bool:
    """Public demo credentials are a DEVELOPMENT convenience only.

    They used to be served on any deployment that had demo data seeded — which
    meant a public URL listed the owner's real password. Now they are off by
    default on any hosted deployment (Vercel / ERP_ENV=production) and must be
    switched on explicitly with ERP_SHOW_DEMO_ACCOUNTS=1.
    """
    flag = os.environ.get("ERP_SHOW_DEMO_ACCOUNTS")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes", "on")
    if os.environ.get("VERCEL") or os.environ.get("ERP_ENV", "").strip().lower() in ("production", "prod"):
        return False
    return os.environ.get("ERP_SEED_DEMO_DATA", "true").lower() not in ("0", "false", "no")


@route("GET", r"/api/dev/accounts", None)
def dev_accounts(ctx):
    """DEMO ONLY — returns the seeded development logins so the login screen can
    offer one-click sign-in. Off on hosted deployments (see demo_accounts_enabled)."""
    if not demo_accounts_enabled():
        raise ApiError("NOT_FOUND: غير متاح / not available", 404)
    path = Path(os.environ.get("ERP_CREDENTIALS_FILE") or (BASE_DIR / "data" / "dev-credentials.txt"))
    conn = read()
    users = {r["username"]: r for r in
             conn.execute("SELECT username, full_name_ar, role_code FROM users").fetchall()}
    role_names = {r["code"]: (r["name_ar"], r["name_en"]) for r in
                  conn.execute("SELECT code, name_ar, name_en FROM roles").fetchall()}
    # NOTE: the password is deliberately NOT returned. The demo list is a picker
    # ("sign in as the auditor"), not a credential dump: filling a password into
    # a public page — even a development one — would put a real-looking password
    # on the screen and in the browser history. The sign-in happens server-side
    # through /api/auth/demo-login instead.
    accounts = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) != 4:
                continue
            name, username, _password, role = parts
            rn = role_names.get(role, (role, role))
            accounts.append({"name_ar": name, "username": username,
                             "role": role, "role_name_ar": rn[0], "role_name_en": rn[1]})
    else:
        # Read-only filesystem (serverless): rebuild the list from the seed table.
        try:
            from seed import DEV_PASSWORDS
        except Exception:
            DEV_PASSWORDS = {}
        for username in DEV_PASSWORDS:
            row = users.get(username)
            if row is None:
                continue
            rn = role_names.get(row["role_code"], (row["role_code"], row["role_code"]))
            accounts.append({"name_ar": row["full_name_ar"], "username": username,
                             "role": row["role_code"],
                             "role_name_ar": rn[0], "role_name_en": rn[1]})
    if not accounts:
        raise ApiError("NOT_FOUND: ملف بيانات التطوير غير موجود / dev credentials missing", 404)
    # The owner account is deliberately absent from the public picker. Its name is
    # the company's own name, so printing it on a sign-in screen tells anyone who
    # reaches the page exactly which account to attack. The owner signs in by
    # typing the username; the picker exists to demo the *role* experience.
    accounts = [a for a in accounts if a.get("role") != "company_owner"]
    accounts.sort(key=lambda a: (a.get("role") in ("auditor", "sales_user"), a.get("username", "")))
    return {"accounts": accounts, "demo": True,
            "warning": "Development picker — one click signs in as that seeded user. "
                       "Never enable this endpoint in production."}


@route("POST", r"/api/auth/demo-login")
def demo_login(ctx):
    """One-click sign-in for the development picker.

    The password never leaves the server: the caller sends only the *username*
    it picked, the credentials file is read here, and the normal login path is
    executed. Disabled anywhere ``/api/dev/accounts`` is disabled.
    """
    if not demo_accounts_enabled():
        raise ApiError("NOT_FOUND: غير متاح / not available", 404)
    username = (ctx.body.get("username") or "").strip()[:80]
    if not username:
        raise ApiError("MISSING_FIELDS: اختر حسابًا / Pick an account", 400)
    # Same rule as the picker: the owner account is not reachable by one click.
    owner = read().execute(
        "SELECT 1 FROM users WHERE username=? AND role_code='company_owner'", (username,)).fetchone()
    if owner:
        raise ApiError("FORBIDDEN: هذا الحساب لا يُدخل بضغطة واحدة / This account must sign in with its password", 403)
    path = Path(os.environ.get("ERP_CREDENTIALS_FILE") or (BASE_DIR / "data" / "dev-credentials.txt"))
    password = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 4 and parts[1] == username:
                password = parts[2]
                break
    if password is None:
        try:
            from seed import DEV_PASSWORDS
            password = DEV_PASSWORDS.get(username)
        except Exception:
            password = None
    if password is None:
        raise ApiError("NOT_FOUND: الحساب التجريبي غير موجود / Demo account not found", 404)
    ctx.body = {"username": username, "password": password}
    return login(ctx)


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


_STORAGE_FLAG = {"at": 0.0, "durable": True}


def _storage_durable() -> bool:
    """`durable` never changes while the process lives — resolve it once.

    The login screen polls /api/health; computing it each time meant the D1
    backend answered with six COUNT(*) round-trips that nobody uses.
    """
    import time as _time
    if _time.monotonic() - _STORAGE_FLAG["at"] > 300:
        try:
            _STORAGE_FLAG["durable"] = bool(db.storage_info()["durable"])
        except Exception:
            _STORAGE_FLAG["durable"] = True
        _STORAGE_FLAG["at"] = _time.monotonic()
    return _STORAGE_FLAG["durable"]


@route("GET", r"/api/health")
def health(ctx):
    # dev_mode tells the login screen it may show the demo accounts panel.
    dev_mode = demo_accounts_enabled()
    return {"status": "ok", "time": utcnow(), "version": "1.1.0",
            "features": {"pdf": True, "xlsx": True, "einvoice": True},
            "dev_mode": dev_mode,
            "storage": {"durable": _storage_durable()}}


# -------------------------- storage / backups ------------------------------
@route("GET", r"/api/admin/storage", "settings.manage")
def admin_storage(ctx):
    """Where the database lives, whether it survives a restart, row counts."""
    info = db.storage_info()
    info["backend"] = db.backend_name()
    info["backup_dir"] = str(BACKUP_DIR)
    try:
        files = sorted(list(BACKUP_DIR.glob("erp-*.db")) + list(BACKUP_DIR.glob("erp-*.sql")),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:10]
        info["backups"] = [{"name": f.name, "size_bytes": f.stat().st_size,
                            "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
                           for f in files]
    except OSError:
        info["backups"] = []
    return info


@route("GET", r"/api/admin/integrity", "settings.manage")
def admin_integrity(ctx):
    """Run the accounting invariants against the live database (audited).

    This is the safety net for the Cloudflare D1 backend: an HTTP database has no
    interactive transactions, so every write is verified here — balanced ledger,
    two-sided entries, no orphans, unique document numbers, flat trial balance.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ci", BASE_DIR / "scripts" / "check_integrity.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)

    results = [{"check": name, "passed": bool(ok), "detail": detail}
               for name, ok, detail in ci.run(ctx.conn)]
    failed = [r for r in results if not r["passed"]]
    audit(ctx.conn, ctx.user, "admin.integrity", "database", 0,
          {"failed": [r["check"] for r in failed], "backend": db.backend_name()},
          company_id=ctx.user["company_id"])
    return {"backend": db.backend_name(), "passed": not failed,
            "checks_passed": len(results) - len(failed), "checks_total": len(results),
            "results": results}


@route("GET", r"/api/admin/backup", "settings.manage")
def admin_backup(ctx):
    """Download a consistent snapshot of the whole database (audited)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = ".sql" if db.backend_name() == "d1" else ".db"
    tmp = BACKUP_DIR / f"erp-{stamp}{ext}"
    db.snapshot(tmp)
    audit(ctx.conn, ctx.user, "admin.backup", "database", 0,
          {"file": tmp.name, "size": tmp.stat().st_size, "backend": db.backend_name()},
          company_id=ctx.user["company_id"])
    return {"__file__": True, "content": tmp.read_bytes(),
            "content_type": "application/sql" if ext == ".sql" else "application/x-sqlite3",
            "filename": f"eltaranisi-erp-backup-{stamp}{ext}"}


@route("POST", r"/api/admin/backup", "settings.manage")
def admin_backup_now(ctx):
    """Take a server-side snapshot into BACKUP_DIR without downloading it."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = ".sql" if db.backend_name() == "d1" else ".db"
    tmp = BACKUP_DIR / f"erp-{stamp}{ext}"
    db.snapshot(tmp)
    audit(ctx.conn, ctx.user, "admin.backup.server", "database", 0,
          {"file": tmp.name, "size": tmp.stat().st_size}, company_id=ctx.user["company_id"])
    return {"ok": True, "file": tmp.name, "size_bytes": tmp.stat().st_size,
            "backup_dir": str(BACKUP_DIR)}


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
    # only the code and the Arabic name are mandatory: a supplier is often known
    # by its Arabic trade name alone, so the English name falls back to it
    for f in ("code", "name_ar"):
        if not b.get(f):
            raise ApiError(f"MISSING_FIELD: {f} مطلوب / required")
    b["name_en"] = (b.get("name_en") or "").strip() or b["name_ar"]
    if q1(ctx.conn, "SELECT 1 FROM vendors WHERE company_id=? AND code=?", (cid, b["code"])):
        raise ApiError("DUPLICATE: كود المورد مستخدم / Vendor code already exists")
    ap_acc = q1(ctx.conn, "SELECT id FROM accounts WHERE company_id=? AND code='2110'", (cid,))["id"]
    cur = ctx.conn.execute("""INSERT INTO vendors (company_id, code, name_ar, name_en, phone, email, tax_number,
        payment_terms_days, account_id) VALUES (?,?,?,?,?,?,?,?,?)""",
        (cid, b["code"], b["name_ar"], b["name_en"], b.get("phone"), b.get("email"), b.get("tax_number"),
         int(b.get("payment_terms_days") or 30), ap_acc))
    audit(ctx.conn, ctx.user, "vendors.create", "vendor", cur.lastrowid, b, company_id=cid)
    return {"id": cur.lastrowid}


@route("GET", r"/api/vendors/(\d+)", "vendors.view")
def vendor_detail(ctx, vendor_id):
    """One vendor with its outstanding balance, payment history and open invoices.

    This is what the vendor edit panel on screen reads, so the balance and the
    payments are always the server's numbers, never a browser-side guess.
    """
    cid = ensure_company(ctx.user)
    v = q1(ctx.conn, "SELECT * FROM vendors WHERE id=? AND company_id=?", (vendor_id, cid))
    if v is None:
        raise ApiError("NOT_FOUND: المورد غير موجود / Vendor not found", 404)
    invoices = qall(ctx.conn, """SELECT id, invoice_no, invoice_date, due_date, total_cents,
            paid_cents, total_cents - paid_cents AS remaining_cents, status
        FROM purchase_invoices WHERE vendor_id=? AND company_id=?
        ORDER BY invoice_date DESC, id DESC""", (vendor_id, cid))
    outstanding = sum(i["remaining_cents"] for i in invoices
                      if i["status"] in ("posted", "partially_paid"))
    payments = qall(ctx.conn, """SELECT p.id, p.payment_no, p.payment_date, p.amount_cents,
            p.allocated_cents, p.amount_cents - p.allocated_cents AS unallocated_cents,
            p.method, p.status, p.reference, p.notes
        FROM payments p WHERE p.company_id=? AND p.party_type='vendor' AND p.party_id=?
        ORDER BY p.payment_date DESC, p.id DESC""", (cid, vendor_id))
    return {
        "vendor": dict(v),
        "outstanding_cents": outstanding,
        "invoices": invoices,
        "open_invoices": [i for i in invoices if i["status"] in ("posted", "partially_paid")],
        "payments": payments,
        "paid_total_cents": sum(p["amount_cents"] for p in payments if p["status"] == "posted"),
    }


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
@route("GET", r"/api/payments/bootstrap", "payments.view")
def payments_bootstrap(ctx):
    """Everything the receipt/payment form needs — in ONE request.

    The form used to fire five separate requests (banks, customers, vendors,
    sales invoices, purchase invoices). On the serverless deployment each one is
    a full round-trip plus its own D1 queries, so opening the form took ~1.4 s
    before anything useful appeared. Sections the user may not read are skipped
    instead of failing the whole request.
    """
    def safe(fn, empty):
        try:
            return fn(ctx)
        except (PermissionError_, AuthError):
            return empty
    return {
        "banks": safe(banks, {"accounts": [], "position": {}}),
        "customers": safe(customers_list, {"customers": []}),
        "vendors": safe(vendors_list, {"vendors": []}),
        "sales_invoices": safe(sales_list, {"invoices": []}),
        "purchase_invoices": safe(purchases_list, {"invoices": []}),
    }


@route("GET", r"/api/payments", "payments.view")
def payments_list(ctx):
    cid = ensure_company(ctx.user)
    rows = qall(ctx.conn, """SELECT p.*, a.code AS gl_code, a.name_ar AS gl_name,
            CASE WHEN p.party_type='customer' THEN (SELECT name_ar FROM customers WHERE id=p.party_id)
                 WHEN p.party_type='vendor' THEN (SELECT name_ar FROM vendors WHERE id=p.party_id) END AS party_name
        FROM payments p JOIN accounts a ON a.id=p.gl_account_id
        WHERE p.company_id=? ORDER BY p.payment_date DESC, p.id DESC LIMIT 300""", (cid,))
    return {"payments": rows}


PAYMENT_METHODS = {"cash": "نقدًا", "bank": "تحويل بنكي", "cheque": "شيك", "transfer": "تحويل"}


def _payment_method(raw):
    """Normalise the payment method; anything unknown falls back to a bank transfer."""
    method = str(raw or "bank").strip().lower()
    aliases = {"cash": "cash", "نقدا": "cash", "نقدًا": "cash", "نقداً": "cash", "نقد": "cash",
               "bank": "bank", "transfer": "transfer", "تحويل": "transfer", "تحويل بنكي": "transfer",
               "cheque": "cheque", "check": "cheque", "شيك": "cheque"}
    return aliases.get(method, "bank")


@route("POST", r"/api/payments", "payments.create")
def payments_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    pay_id = documents.create_payment(ctx.conn, ctx.user, cid, {
        "payment_type": b.get("payment_type", "receipt"),
        "payment_date": b.get("payment_date") or date.today().isoformat(),
        "party_type": b.get("party_type"), "party_id": b.get("party_id"),
        "method": _payment_method(b.get("method")), "gl_account_id": int(b["gl_account_id"]),
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


@route("POST", r"/api/bank-transactions/(\d+)/unreconcile", "banks.manage")
def bank_unreconcile(ctx, txn_id):
    """Undo a reconciliation so the voucher behind it can be edited or deleted."""
    cid = ensure_company(ctx.user)
    t = q1(ctx.conn, "SELECT * FROM bank_transactions WHERE id=? AND company_id=?", (txn_id, cid))
    if t is None:
        raise ApiError("NOT_FOUND: الحركة غير موجودة / Transaction not found", 404)
    if not t["is_reconciled"]:
        raise ApiError("INVALID_STATE: الحركة غير مطابَقة أصلًا / Transaction is not reconciled")
    ctx.conn.execute("""UPDATE bank_transactions SET is_reconciled=0, matched_payment_id=NULL,
        reconciled_by=NULL, reconciled_at=NULL WHERE id=?""", (txn_id,))
    audit(ctx.conn, ctx.user, "bank_txn.unreconcile", "bank_transaction", txn_id,
          {"payment_id": t["matched_payment_id"]}, company_id=cid)
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



# ------------------------------- إعاشة العاملين ---------------------------
# Staff subsistence (meals) allowance — a monthly sheet per department that
# mirrors the paper form and posts one balanced entry to the ledger.
@route("GET", r"/api/allowance/meta", "allowance.view")
def allowance_meta(ctx):
    cid = ensure_company(ctx.user)
    return {
        "departments": allowance.departments(ctx.conn, cid),
        "all_departments": allowance.ALL_DEPARTMENTS,
        "people": allowance.list_people(ctx.conn, cid),
        "code_labels": {k: {"ar": v[0], "en": v[1]} for k, v in allowance.CODE_LABELS.items()},
        "accounts": allowance.ACCOUNTS,
    }


@route("GET", r"/api/allowance/periods", "allowance.view")
def allowance_periods(ctx):
    cid = ensure_company(ctx.user)
    sql = "SELECT * FROM allowance_periods WHERE company_id=?"
    params = [cid]
    if ctx.qs.get("department"):
        sql += " AND department=?"
        params.append(ctx.qs["department"])
    if ctx.qs.get("from"):
        sql += " AND end_date>=?"
        params.append(ctx.qs["from"])
    if ctx.qs.get("to"):
        sql += " AND start_date<=?"
        params.append(ctx.qs["to"])
    sql += " ORDER BY start_date DESC, department LIMIT 300"
    return {"periods": qall(ctx.conn, sql, tuple(params))}


@route("POST", r"/api/allowance/periods", "allowance.manage")
def allowance_period_create(ctx):
    cid = ensure_company(ctx.user)
    b = ctx.body
    res = allowance.create_period(ctx.conn, ctx.user, cid, (b.get("department") or "").strip(),
                                  b.get("start_date") or date.today().replace(day=1).isoformat(),
                                  b.get("end_date") or date.today().isoformat(),
                                  branch_id=b.get("branch_id"),
                                  copy_roster=b.get("copy_roster", True),
                                  att_from=b.get("att_from"), att_to=b.get("att_to"))
    audit(ctx.conn, ctx.user, "allowance.create", "allowance_period", res["id"],
          {"department": res.get("department") or b.get("department"),
           "from": b.get("start_date"), "to": b.get("end_date")},
          company_id=cid)
    return res


@route("GET", r"/api/allowance/periods/([0-9]+)", "allowance.view")
def allowance_period_get(ctx, period_id):
    cid = ensure_company(ctx.user)
    data = allowance.get_period(ctx.conn, cid, int(period_id))
    data["people"] = allowance.list_people(ctx.conn, cid, data["department"])
    return data


@route("PUT", r"/api/allowance/periods/([0-9]+)", "allowance.manage")
def allowance_period_save(ctx, period_id):
    cid = ensure_company(ctx.user)
    stats = allowance.save_period(ctx.conn, ctx.user, cid, int(period_id), ctx.body or {})
    audit(ctx.conn, ctx.user, "allowance.save", "allowance_period", int(period_id), stats, company_id=cid)
    return {"ok": True, **stats}


@route("POST", r"/api/allowance/periods/([0-9]+)/window", "allowance.manage")
def allowance_period_window(ctx, period_id):
    """نطاق الحضور: احسب أيام الحضور ديناميكيًا من تاريخ إلى تاريخ."""
    cid = ensure_company(ctx.user)
    b = ctx.body or {}
    res = allowance.set_window(ctx.conn, ctx.user, cid, int(period_id),
                               b.get("att_from"), b.get("att_to"), b.get("line_ids"))
    audit(ctx.conn, ctx.user, "allowance.window", "allowance_period", int(period_id),
          {"from": b.get("att_from"), "to": b.get("att_to"), "rows": res.get("updated")},
          company_id=cid)
    return res


@route("DELETE", r"/api/allowance/periods/([0-9]+)", "allowance.manage")
def allowance_period_delete(ctx, period_id):
    cid = ensure_company(ctx.user)
    res = allowance.delete_period(ctx.conn, cid, int(period_id))
    audit(ctx.conn, ctx.user, "allowance.delete", "allowance_period", int(period_id), {}, company_id=cid)
    return res


@route("POST", r"/api/allowance/periods/([0-9]+)/post", "allowance.post")
def allowance_period_post(ctx, period_id):
    cid = ensure_company(ctx.user)
    res = allowance.post_period(ctx.conn, ctx.user, cid, int(period_id),
                                (ctx.body or {}).get("entry_date"))
    audit(ctx.conn, ctx.user, "allowance.post", "allowance_period", int(period_id), res, company_id=cid)
    return res


@route("GET", r"/api/allowance/periods/([0-9]+)/xlsx", "allowance.view")
def allowance_period_xlsx(ctx, period_id):
    cid = ensure_company(ctx.user)
    period = allowance.get_period(ctx.conn, cid, int(period_id))
    with_grid = (ctx.qs.get("grid") or "1") not in ("0", "false")
    matrix = allowance.export_matrix(period, with_grid=with_grid)
    title = f"إعاشة {period['department']} {period['start_date']} — {period['end_date']}"
    rows = matrix["rows"] + [matrix["total_row"]]
    head = matrix["head"]
    money_cols = [i for i, h in enumerate(head) if h in ("إجمالى المستحق", "تطبيقات", "الصافى")]
    out = exporter.report_xlsx(title, head, rows, rtl=True, widths=allowance.column_widths(head),
                               font_size=9, wrap_cols=(head.index("الاسم"),),
                               row_height=24, money_cols=money_cols)
    audit(ctx.conn, ctx.user, "allowance.xlsx", "allowance_period", int(period_id), {}, company_id=cid)
    return {"__file__": True, "content": out,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"allowance-{period_id}.xlsx"}


@route("GET", r"/api/allowance/periods/([0-9]+)/pdf", "allowance.view")
def allowance_period_pdf(ctx, period_id):
    cid = ensure_company(ctx.user)
    period = allowance.get_period(ctx.conn, cid, int(period_id))
    lang = _lang(ctx)
    ar = lang != "en"
    with_grid = (ctx.qs.get("grid") or "1") not in ("0", "false")
    matrix = allowance.export_matrix(period, with_grid=with_grid)
    head = matrix["head"]
    labels = allowance.print_labels(head)
    cols = [{"ar": lbl, "en": lbl, "align": "start" if h in ("الاسم", "القسم") else "center",
             "rtl": h in ("الاسم", "القسم")} for h, lbl in zip(head, labels)]
    rows = matrix["rows"] + [matrix["total_row"]]
    company = exporter.company_block(ctx.conn, cid)
    out = exporter.report_pdf(
        f"إعاشة {period['department']}", f"Subsistence — {period['department']}", cols, rows,
        company=company, lang=lang,
        subtitle_ar=f"الفترة من {exporter.fmt_date_ui(period['start_date'])} إلى {exporter.fmt_date_ui(period['end_date'])}",
        subtitle_en=f"{period['start_date']} — {period['end_date']}",
        landscape=True, margin=18, col_widths=allowance.column_points(head, 806),
        cell_size=7.2, header_size=7.2, row_h=14.6, cell_floor=4.2, band_rows=True)
    audit(ctx.conn, ctx.user, "allowance.pdf", "allowance_period", int(period_id), {}, company_id=cid)
    return {"__file__": True, "content": out, "content_type": "application/pdf",
            "filename": f"allowance-{period_id}.pdf", "inline": True}


@route("GET", r"/api/allowance/people", "allowance.view")
def allowance_people(ctx):
    cid = ensure_company(ctx.user)
    return {"people": allowance.list_people(ctx.conn, cid, ctx.qs.get("department")),
            "departments": allowance.departments(ctx.conn, cid)}


@route("POST", r"/api/allowance/people", "allowance.manage")
def allowance_person_upsert(ctx):
    cid = ensure_company(ctx.user)
    res = allowance.upsert_person(ctx.conn, cid, ctx.body or {})
    audit(ctx.conn, ctx.user, "allowance.person", "allowance_person", res["id"], ctx.body or {}, company_id=cid)
    return res


@route("POST", r"/api/allowance/people/([0-9]+)/active", "allowance.manage")
def allowance_person_active(ctx, person_id):
    cid = ensure_company(ctx.user)
    res = allowance.set_people_active(ctx.conn, cid, int(person_id), bool((ctx.body or {}).get("active", True)))
    audit(ctx.conn, ctx.user, "allowance.person.active", "allowance_person", int(person_id),
          res, company_id=cid)
    return res


@route("POST", r"/api/allowance/people/import", "allowance.manage")
def allowance_people_import(ctx):
    """Import the roster (names + daily rate) from the sample CSV / posted rows."""
    cid = ensure_company(ctx.user)
    rows = (ctx.body or {}).get("rows")
    if not rows:
        path = BASE_DIR / "seed_data" / "allowance_roster.csv"
        if not path.exists():
            path = BASE_DIR / "samples" / "allowance_roster.csv"
        if not path.exists():
            raise ApiError("NOT_FOUND: ملف الكشف غير موجود / Roster file not found", 404)
        import csv as _csv
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [{"department": r["department"], "name_ar": r["name_ar"],
                     "daily_rate": float(r["daily_rate"] or 30)} for r in _csv.DictReader(fh)]
    res = allowance.import_roster(ctx.conn, cid, rows)
    audit(ctx.conn, ctx.user, "allowance.import", "allowance_people", None, res, company_id=cid)
    return res


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


# ------------------------- debt / مديونية --------------------------------
# Contractor & supplier statements billed by quantity (طن، نقلة، حاوية، ساعة،
# إيجار شهري) with settlement vouchers, mirroring the paper debt statement.
@route("GET", r"/api/debt/meta", "debt.view")
def debt_meta(ctx):
    cid = ensure_company(ctx.user)
    cash = qall(ctx.conn, "SELECT id, code, name_ar, name_en FROM accounts WHERE company_id=? "
                          "AND is_postable=1 AND (code LIKE '111%' OR code LIKE '112%') ORDER BY code", (cid,))
    expense = qall(ctx.conn, "SELECT id, code, name_ar, name_en FROM accounts WHERE company_id=? "
                             "AND is_postable=1 AND code LIKE '5%' ORDER BY code", (cid,))
    vendors = qall(ctx.conn, "SELECT id, code, name_ar, name_en FROM vendors WHERE company_id=? "
                             "AND is_active=1 ORDER BY code", (cid,))
    return {
        "kinds": [{"code": k, "label_ar": v[0], "label_en": v[1]} for k, v in debt.KINDS.items()],
        "units": [{"code": k, "label_ar": v[0], "label_en": v[1]} for k, v in debt.UNITS.items()],
        "methods": [{"code": k, "label_ar": v} for k, v in debt.METHODS.items()],
        "cash_accounts": cash, "expense_accounts": expense, "vendors": vendors,
    }


@route("GET", r"/api/debt/invoices", "debt.view")
def debt_invoices(ctx):
    """بيان الفواتير — every due line of every contractor in one list."""
    cid = ensure_company(ctx.user)
    acc = ctx.qs.get("account_id")
    return debt.invoices_statement(ctx.conn, cid, ctx.qs.get("from") or None,
                                   ctx.qs.get("to") or None, int(acc) if acc else None)


@route("GET", r"/api/debt/invoices/xlsx", "debt.view")
def debt_invoices_xlsx(ctx):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    ar = lang != "en"
    acc = ctx.qs.get("account_id")
    data = debt.invoices_statement(ctx.conn, cid, ctx.qs.get("from") or None,
                                   ctx.qs.get("to") or None, int(acc) if acc else None)
    head = ["التاريخ", "الحساب", "البيان", "العملية", "الكمية", "الوحدة", "فئة الوحدة",
            "الإجمالي المستحق", "المركب", "العميل", "بيان التشغيل"] if ar else \
           ["Date", "Account", "Statement", "Operation", "Qty", "Unit", "Rate",
            "Amount due", "Vessel", "Client", "Report no."]
    rows = [head]
    for it in sorted(data["items"], key=lambda x: (x["doc_date"], x["id"])):
        rows.append([exporter.fmt_date_ui(it["doc_date"]), it["account_name"], it["description"], it["operation"] or "",
                     f"{it['qty_milli'] / 1000:g}" if it["qty_milli"] else "",
                     it["unit_label_ar"] if ar else it["unit_label_en"],
                     money_str(it["unit_rate_cents"]) if it["unit_rate_cents"] else "",
                     money_str(it["amount_cents"]), it["vessel"] or "", it["client_name"] or "",
                     it["report_no"] or ""])
    rows.append(["", "", "إجمالي بيان الفواتير" if ar else "Total due lines", "", "", "", "",
                 money_str(data["totals"]["amount_cents"]), "", "", ""])
    out = exporter.report_xlsx("بيان الفواتير" if ar else "Invoice statement", rows[0], rows[1:], rtl=ar)
    audit(ctx.conn, ctx.user, "debt_invoices.xlsx", "debt_invoice_statement", None, {}, company_id=cid)
    return {"__file__": True, "content": out,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": "debt-invoices.xlsx"}


@route("GET", r"/api/debt/invoices/pdf", "debt.view")
def debt_invoices_pdf(ctx):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    ar = lang != "en"
    acc = ctx.qs.get("account_id")
    data = debt.invoices_statement(ctx.conn, cid, ctx.qs.get("from") or None,
                                   ctx.qs.get("to") or None, int(acc) if acc else None)
    cols = [{"ar": "التاريخ", "en": "Date", "w": 1.0, "align": "center", "rtl": False},
            {"ar": "الحساب", "en": "Account", "w": 2.2},
            {"ar": "البيان", "en": "Statement", "w": 2.6},
            {"ar": "الكمية", "en": "Qty", "w": 1.1, "align": "center", "rtl": False},
            {"ar": "فئة الوحدة", "en": "Rate", "w": 1.0, "align": "center", "rtl": False},
            {"ar": "الإجمالي المستحق", "en": "Due", "w": 1.3, "align": "center", "rtl": False}]
    rows = []
    for it in sorted(data["items"], key=lambda x: (x["doc_date"], x["id"])):
        qty = f"{it['qty_milli'] / 1000:g}" if it["qty_milli"] else ""
        rows.append([exporter.fmt_date_ui(it["doc_date"]), it["account_name"], it["description"],
                     f"{qty} {it['unit_label_ar'] if ar else it['unit_label_en']}".strip(),
                     money_str(it["unit_rate_cents"]) if it["unit_rate_cents"] else "",
                     money_str(it["amount_cents"])])
    rows.append(["", "", "إجمالي بيان الفواتير" if ar else "Total due lines", "", "",
                 money_str(data["totals"]["amount_cents"])])
    company = exporter.company_block(ctx.conn, cid)
    out = exporter.report_pdf("بيان الفواتير", "Invoice statement", cols, rows, company=company, lang=lang,
                              subtitle_ar=f"من {exporter.fmt_date_ui(data['range']['from']) if data['range']['from'] else 'البداية'} إلى {exporter.fmt_date_ui(data['range']['to']) if data['range']['to'] else 'اليوم'}",
                              subtitle_en=f"{data['range']['from'] or 'start'} — {data['range']['to'] or 'today'}")
    audit(ctx.conn, ctx.user, "debt_invoices.pdf", "debt_invoice_statement", None, {}, company_id=cid)
    return {"__file__": True, "content": out, "content_type": "application/pdf",
            "filename": "debt-invoices.pdf", "inline": True}


@route("GET", r"/api/debt/summary", "debt.view")
def debt_summary(ctx):
    cid = ensure_company(ctx.user)
    d1 = ctx.qs.get("from") or None
    d2 = ctx.qs.get("to") or None
    return debt.summary(ctx.conn, cid, d1, d2)


@route("GET", r"/api/debt/summary/xlsx", "debt.view")
def debt_summary_xlsx(ctx):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    ar = lang != "en"
    d1 = ctx.qs.get("from") or None
    d2 = ctx.qs.get("to") or None
    data = debt.summary(ctx.conn, cid, d1, d2)
    head = ["م", "البيان"] if ar else ["#", "Statement"]
    head += ["النوع", "وحدة الاحتساب", "المستحق", "المدفوع", "دفعات مقدمة", "الصافي"] if ar else \
            ["Type", "Billing unit", "Due", "Paid", "Advances", "Net"]
    rows, i = [head], 0
    for g in data["groups"]:
        for a in g["accounts"]:
            i += 1
            rows.append([i, a["name_ar"] if ar else (a["name_en"] or a["name_ar"]),
                         g["label_ar"] if ar else g["label_en"],
                         a["unit_label_ar"] if ar else a["unit_label_en"],
                         money_str(a["due_cents"]), money_str(a["paid_cents"]),
                         money_str(a["advance_cents"]), money_str(a["net_cents"])])
        rows.append(["", ("اجمالي " if ar else "Total ") + (g["label_ar"] if ar else g["label_en"]), "", "",
                     money_str(g["due_cents"]), money_str(g["paid_cents"]),
                     money_str(g["advance_cents"]), money_str(g["net_cents"])])
    t = data["totals"]
    rows.append(["", "إجمالي المديونية" if ar else "Total debt", "", "",
                 money_str(t["due_cents"]), money_str(t["paid_cents"]),
                 money_str(t["advance_cents"]), money_str(t["net_cents"])])
    out = exporter.report_xlsx("حساب المديونية" if ar else "Debt summary", rows[0], rows[1:], rtl=ar)
    audit(ctx.conn, ctx.user, "debt_summary.xlsx", "debt_summary", None, {}, company_id=cid)
    return {"__file__": True, "content": out,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": "debt-summary.xlsx"}


@route("GET", r"/api/debt/summary/pdf", "debt.view")
def debt_summary_pdf(ctx):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    ar = lang != "en"
    d1 = ctx.qs.get("from") or None
    d2 = ctx.qs.get("to") or None
    data = debt.summary(ctx.conn, cid, d1, d2)
    cols = [{"ar": "م", "en": "#", "w": 0.5, "align": "center", "rtl": False},
            {"ar": "البيان", "en": "Statement", "w": 3.0},
            {"ar": "النوع", "en": "Type", "w": 1.3},
            {"ar": "المستحق", "en": "Due", "w": 1.3, "align": "center", "rtl": False},
            {"ar": "المدفوع", "en": "Paid", "w": 1.3, "align": "center", "rtl": False},
            {"ar": "دفعات مقدمة", "en": "Advances", "w": 1.3, "align": "center", "rtl": False},
            {"ar": "الصافي", "en": "Net", "w": 1.3, "align": "center", "rtl": False}]
    rows, i = [], 0
    for g in data["groups"]:
        for a in g["accounts"]:
            i += 1
            rows.append([str(i), a["name_ar"] if ar else a["name_en"] or a["name_ar"],
                         g["label_ar"] if ar else g["label_en"],
                         money_str(a["due_cents"]), money_str(a["paid_cents"]),
                         money_str(a["advance_cents"]), money_str(a["net_cents"])])
        rows.append(["", ("اجمالي " if ar else "Total ") + (g["label_ar"] if ar else g["label_en"]), "", "",
                     money_str(g["due_cents"]), money_str(g["paid_cents"]),
                     money_str(g["advance_cents"]), money_str(g["net_cents"])])
    t = data["totals"]
    rows.append(["", "إجمالي المديونية" if ar else "Total debt", "", "",
                 money_str(t["due_cents"]), money_str(t["paid_cents"]),
                 money_str(t["advance_cents"]), money_str(t["net_cents"])])
    company = exporter.company_block(ctx.conn, cid)
    out = exporter.report_pdf("حساب المديونية", "Debt summary", cols, rows, company=company, lang=lang,
                              subtitle_ar=f"من {exporter.fmt_date_ui(d1) if d1 else 'البداية'} إلى {exporter.fmt_date_ui(d2) if d2 else 'اليوم'}",
                              subtitle_en=f"{d1 or 'start'} — {d2 or 'today'}")
    audit(ctx.conn, ctx.user, "debt_summary.pdf", "debt_summary", None, {}, company_id=cid)
    return {"__file__": True, "content": out, "content_type": "application/pdf",
            "filename": "debt-summary.pdf", "inline": True}


@route("GET", r"/api/debt/accounts", "debt.view")
def debt_accounts_list(ctx):
    cid = ensure_company(ctx.user)
    return {"accounts": debt.list_accounts(ctx.conn, cid)}


@route("POST", r"/api/debt/accounts", "debt.create")
def debt_accounts_create(ctx):
    cid = ensure_company(ctx.user)
    new_id = debt.create_account(ctx.conn, ctx.user, cid, ctx.body)
    notify(ctx.conn, cid, ctx.user["id"], "create", "تمت الإضافة", "Created",
           f"حساب مديونية جديد: {ctx.body.get('name_ar')}",
           f"New debt account: {ctx.body.get('name_ar')}")
    return {"id": new_id}


@route("GET", r"/api/debt/accounts/(\d+)", "debt.view")
def debt_account_statement(ctx, account_id):
    cid = ensure_company(ctx.user)
    return debt.statement(ctx.conn, cid, int(account_id),
                          ctx.qs.get("from") or None, ctx.qs.get("to") or None)


@route("PATCH", r"/api/debt/accounts/(\d+)", "debt.edit")
def debt_accounts_update(ctx, account_id):
    cid = ensure_company(ctx.user)
    return debt.update_account(ctx.conn, ctx.user, cid, int(account_id), ctx.body)


@route("DELETE", r"/api/debt/accounts/(\d+)", "debt.delete")
def debt_accounts_delete(ctx, account_id):
    return _delete_document(ctx, lambda conn, user, cid, rid, **kw: debt.delete_account(conn, user, cid, rid),
                            int(account_id))


@route("GET", r"/api/debt/accounts/(\d+)/statement/xlsx", "debt.view")
def debt_statement_xlsx(ctx, account_id):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    ar = lang != "en"
    st = debt.statement(ctx.conn, cid, int(account_id), ctx.qs.get("from") or None, ctx.qs.get("to") or None)
    acc = st["account"]
    title = ("كشف حساب " if ar else "Statement — ") + (acc["name_ar"] if ar else acc["name_en"] or acc["name_ar"])
    cols = ["التاريخ", "البيان", "العملية", "الكمية", "الوحدة", "فئة الوحدة", "المستحق"] if ar else \
           ["Date", "Statement", "Operation", "Qty", "Unit", "Unit rate", "Due"]
    rows = [cols]
    for it in sorted(st["items"], key=lambda x: (x["doc_date"], x["id"])):
        rows.append([it["doc_date"], it["description"], it["operation"] or "",
                     f"{it['qty_milli'] / 1000:g}" if it["qty_milli"] else "",
                     it["unit_label_ar"] if ar else it["unit_label_en"],
                     money_str(it["unit_rate_cents"]) if it["unit_rate_cents"] else "",
                     money_str(it["amount_cents"])])
    rows.append(["", "إجمالي المستحق" if ar else "Total due", "", "", "", "", money_str(st["totals"]["due_cents"])])
    rows.append([])
    rows.append(["اذن صرف", "التاريخ", "المبلغ", "البيان", "طريقة السداد", "المستلم/الصارف"] if ar else
                ["Voucher", "Date", "Amount", "Statement", "Method", "Payee"])
    for p in sorted(st["payments"], key=lambda x: (x["pay_date"], x["id"])):
        rows.append([p["voucher_no"] or p["payment_no"], exporter.fmt_date_ui(p["pay_date"]), money_str(p["amount_cents"]),
                     p["description"] or ("دفعة مقدمة" if p["advance_cents"] else ""),
                     p["method_label_ar"], p["payee"] or ""])
    rows.append(["", "اجمالي الدفعات المستلمة" if ar else "Total paid", money_str(st["totals"]["paid_cents"]), "", "", ""])
    if st["totals"]["advance_cents"]:
        rows.append(["", "دفعات مقدمة (لم تُقابلها فاتورة)" if ar else "Advances (no invoice yet)",
                     money_str(st["totals"]["advance_cents"]), "", "", ""])
    rows.append(["", "صافي المستحق" if ar else "Net due", money_str(st["totals"]["net_cents"]), "", "", ""])
    out = exporter.report_xlsx(title, rows[0], rows[1:], rtl=ar)
    audit(ctx.conn, ctx.user, "debt_statement.xlsx", "debt_account", int(account_id), {}, company_id=cid)
    return {"__file__": True, "content": out,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"debt-{acc['code']}.xlsx"}


@route("GET", r"/api/debt/accounts/(\d+)/statement/pdf", "debt.view")
def debt_statement_pdf(ctx, account_id):
    cid = ensure_company(ctx.user)
    lang = _lang(ctx)
    ar = lang != "en"
    st = debt.statement(ctx.conn, cid, int(account_id), ctx.qs.get("from") or None, ctx.qs.get("to") or None)
    acc = st["account"]
    cols = [{"ar": "التاريخ", "en": "Date", "w": 1.1, "align": "center", "rtl": False},
            {"ar": "البيان", "en": "Statement", "w": 3.2},
            {"ar": "الكمية", "en": "Qty", "w": 1.0, "align": "center", "rtl": False},
            {"ar": "فئة الوحدة", "en": "Rate", "w": 1.1, "align": "center", "rtl": False},
            {"ar": "المستحق", "en": "Due", "w": 1.2, "align": "center", "rtl": False}]
    rows = []
    for it in sorted(st["items"], key=lambda x: (x["doc_date"], x["id"])):
        qty = f"{it['qty_milli'] / 1000:g}" if it["qty_milli"] else ""
        unit = (it["unit_label_ar"] if ar else it["unit_label_en"])
        rows.append([exporter.fmt_date_ui(it["doc_date"]), it["description"] + (f" — {it['vessel']}" if it["vessel"] else ""),
                     f"{qty} {unit}".strip(), money_str(it["unit_rate_cents"]) if it["unit_rate_cents"] else "",
                     money_str(it["amount_cents"])])
    rows.append(["", "إجمالي المستحق" if ar else "Total due", "", "", money_str(st["totals"]["due_cents"])])
    rows.append(["", "", "", "الدفعات المستلمة" if ar else "Paid", money_str(st["totals"]["paid_cents"])])
    for p in sorted(st["payments"], key=lambda x: (x["pay_date"], x["id"])):
        voucher = f"اذن صرف {p['voucher_no']}" if p["voucher_no"] else p["payment_no"]
        if p["description"]:
            voucher += f" — {p['description']}"
        rows.append([exporter.fmt_date_ui(p["pay_date"]), voucher, "", p["method_label_ar"], money_str(p["amount_cents"])])
    if st["totals"]["advance_cents"]:
        rows.append(["", "دفعات مقدمة (لم تُقابلها فاتورة)", "", "",
                     money_str(st["totals"]["advance_cents"])])
    rows.append(["", "صافي المستحق" if ar else "Net due", "", "", money_str(st["totals"]["net_cents"])])
    company = exporter.company_block(ctx.conn, cid)
    out = exporter.report_pdf(f"كشف حساب — {acc['name_ar']}", f"Statement — {acc['name_en'] or acc['name_ar']}",
                              cols, rows, company=company, lang=lang,
                              subtitle_ar=f"{acc['code']} • {acc['kind_label_ar']} • من {exporter.fmt_date_ui(st['range']['from']) if st['range']['from'] else 'البداية'} إلى {exporter.fmt_date_ui(st['range']['to']) if st['range']['to'] else 'اليوم'}",
                              subtitle_en=f"{acc['code']} • {acc['kind_label_en']} • {st['range']['from'] or 'start'} — {st['range']['to'] or 'today'}")
    audit(ctx.conn, ctx.user, "debt_statement.pdf", "debt_account", int(account_id), {}, company_id=cid)
    return {"__file__": True, "content": out, "content_type": "application/pdf",
            "filename": f"debt-{acc['code']}.pdf", "inline": True}


@route("GET", r"/api/debt/items", "debt.view")
def debt_items_list(ctx):
    cid = ensure_company(ctx.user)
    acc = ctx.qs.get("account_id")
    return {"items": debt.list_items(ctx.conn, cid, int(acc) if acc else None,
                                     ctx.qs.get("from") or None, ctx.qs.get("to") or None)}


@route("POST", r"/api/debt/items", "debt.create")
def debt_items_create(ctx):
    cid = ensure_company(ctx.user)
    return {"id": debt.create_item(ctx.conn, ctx.user, cid, ctx.body)}


@route("PATCH", r"/api/debt/items/(\d+)", "debt.edit")
def debt_items_update(ctx, item_id):
    cid = ensure_company(ctx.user)
    return debt.update_item(ctx.conn, ctx.user, cid, int(item_id), ctx.body)


@route("DELETE", r"/api/debt/items/(\d+)", "debt.delete")
def debt_items_delete(ctx, item_id):
    return _delete_document(ctx, lambda conn, user, cid, rid, **kw: debt.delete_item(conn, user, cid, rid),
                            int(item_id))


@route("GET", r"/api/debt/payments", "debt.view")
def debt_payments_list(ctx):
    cid = ensure_company(ctx.user)
    acc = ctx.qs.get("account_id")
    return {"payments": debt.list_payments(ctx.conn, cid, int(acc) if acc else None,
                                           ctx.qs.get("from") or None, ctx.qs.get("to") or None)}


@route("POST", r"/api/debt/payments", "debt.create")
def debt_payments_create(ctx):
    cid = ensure_company(ctx.user)
    return {"id": debt.create_payment(ctx.conn, ctx.user, cid, ctx.body)}


@route("PATCH", r"/api/debt/payments/(\d+)", "debt.edit")
def debt_payments_update(ctx, payment_id):
    cid = ensure_company(ctx.user)
    return debt.update_payment(ctx.conn, ctx.user, cid, int(payment_id), ctx.body)


@route("DELETE", r"/api/debt/payments/(\d+)", "debt.delete")
def debt_payments_delete(ctx, payment_id):
    return _delete_document(ctx, lambda conn, user, cid, rid, **kw: debt.delete_payment(conn, user, cid, rid),
                            int(payment_id))


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


# --------------------------------------------------------------------------
# Financial statements (all statements, one shape, with comparatives)
# --------------------------------------------------------------------------
STATEMENT_PERMISSION = {
    "trial-balance": "reports.view", "income-statement": "reports.view",
    "balance-sheet": "reports.view", "tb-balance-sheet": "reports.view",
    "cash-flow": "reports.view", "equity-changes": "reports.view",
    "general-ledger": "reports.view", "journal": "reports.view",
    "receivables-aging": "reports.view", "payables-aging": "reports.view",
    "debt-position": "debt.view",
}


@route("GET", r"/api/statements", "reports.view")
def statements_index(ctx):
    """Catalogue of every statement this build can produce (for the UI)."""
    cid = ensure_company(ctx.user)
    out = []
    for meta in reports.STATEMENT_META:
        perm = STATEMENT_PERMISSION.get(meta["id"], "reports.view")
        out.append({**meta, "perm": perm, "allowed": has(ctx.user, perm)})
    fy = q1(ctx.conn, "SELECT * FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?",
            (cid, date.today().isoformat(), date.today().isoformat()))
    return {"statements": out, "fiscal_year": dict(fy) if fy else None,
            "today": date.today().isoformat()}


@route("GET", r"/api/statements/([a-z][a-z\-]*)", "reports.view")
def statement_detail(ctx, kind):
    cid = ensure_company(ctx.user)
    if kind not in [m["id"] for m in reports.STATEMENT_META]:
        raise ApiError("NOT_FOUND: قائمة غير معروفة / Unknown statement", 404)
    perm = STATEMENT_PERMISSION.get(kind, "reports.view")
    require(ctx.user, perm)
    d1, d2 = _period(ctx.qs)
    compare = (ctx.qs.get("compare") or "").strip().lower() in ("1", "true", "yes", "on")
    company = exporter.company_block(ctx.conn, cid)
    fiscal = q1(ctx.conn, "SELECT * FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?",
                (cid, d2, d2))

    if kind in ("balance-sheet", "tb-balance-sheet"):
        d1 = d2      # a point-in-time statement: the from-date is meaningless
    data = reports.statement(ctx.conn, cid, kind, d1, d2)
    payload = {"kind": kind, "date_from": d1, "date_to": d2, "company": company,
               "fiscal_year": dict(fiscal) if fiscal else None,
               "meta": next(m for m in reports.STATEMENT_META if m["id"] == kind),
               "data": data}
    if compare and kind == "income-statement":
        # previous period of the same length, so "vs last period" is honest
        fmt = "%Y-%m-%d"
        start = datetime.strptime(d1, fmt).date()
        end = datetime.strptime(d2, fmt).date()
        span = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)
        payload["comparison"] = {
            "date_from": prev_start.isoformat(), "date_to": prev_end.isoformat(),
            "data": reports.statement(ctx.conn, cid, kind, prev_start.isoformat(), prev_end.isoformat()),
        }
    audit(ctx.conn, ctx.user, "reports.statement", kind, None,
          {"from": d1, "to": d2, "compare": compare}, company_id=cid)
    return payload


@route("GET", r"/api/statements/([a-z][a-z\-]*)/csv", "reports.export")
def statement_csv(ctx, kind):
    """CSV of any statement — one flat table, UTF-8 BOM so Excel shows Arabic."""
    cid = ensure_company(ctx.user)
    if kind not in [m["id"] for m in reports.STATEMENT_META]:
        raise ApiError("NOT_FOUND: قائمة غير معروفة / Unknown statement", 404)
    require(ctx.user, STATEMENT_PERMISSION.get(kind, "reports.view"))
    d1, d2 = _period(ctx.qs)
    data = reports.statement(ctx.conn, cid, kind, d1, d2)
    header, rows = _statement_table(data, kind)
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    audit(ctx.conn, ctx.user, "reports.export", kind, None,
          {"format": "csv", "from": d1, "to": d2, "rows": len(rows)}, company_id=cid)
    return ctx.file(f"{kind}-{d1}_{d2}.csv", buf.getvalue().encode("utf-8"),
                    "text/csv; charset=utf-8")


def _statement_table(data, kind):
    """Flatten a statement payload into (header, rows) for CSV / Excel / PDF."""
    from db import from_cents

    def money(v):
        return float(from_cents(v or 0))

    if kind == "income-statement":
        header = ["Section", "Code", "Item", "Amount"]
        rows = []
        for key, label in (("revenue", "Revenue"), ("returns", "Returns"),
                           ("cost_of_sales", "Cost of sales")):
            sec = data["sections"][key]
            for l in sec["lines"]:
                rows.append([label, l["code"], l["name_ar"], money(l["amount_cents"])])
            rows.append([label, "", "Total " + label, money(sec["total_cents"])])
        for sec in data["sections"]["operating_expenses"]:
            for l in sec["lines"]:
                rows.append(["Operating expenses", l["code"], l["name_ar"], money(l["amount_cents"])])
        rows.append(["", "", "Gross profit", money(data["gross_profit_cents"])])
        rows.append(["", "", "Net profit", money(data["net_profit_cents"])])
        return header, rows
    if kind in ("balance-sheet", "tb-balance-sheet"):
        header = ["Section", "Code", "Item", "Amount"]
        rows = []
        if kind == "balance-sheet":
            for sec in data["assets_groups"]:
                for l in sec["lines"]:
                    rows.append(["Assets — " + sec["name_en"], l["code"], l["name_ar"], money(l["amount_cents"])])
                rows.append(["Assets — " + sec["name_en"], "", "Total " + sec["name_en"],
                             money(sec["total_cents"])])
            for sec in data["liabilities_groups"]:
                for l in sec["lines"]:
                    rows.append(["Liabilities — " + sec["name_en"], l["code"], l["name_ar"], money(l["amount_cents"])])
                rows.append(["Liabilities — " + sec["name_en"], "", "Total " + sec["name_en"],
                             money(sec["total_cents"])])
            for l in data["equity_group"]["lines"]:
                rows.append(["Equity", l["code"], l["name_ar"], money(l["amount_cents"])])
        else:
            for section, label in (("assets", "Assets"), ("liabilities", "Liabilities"),
                                   ("equity", "Equity")):
                for l in data[section]:
                    rows.append([label, l["code"], l["name_ar"], money(l["amount_cents"])])
        rows.append(["", "", "Total assets", money(data["assets_total_cents"])])
        rows.append(["", "", "Total liabilities", money(data["liabilities_total_cents"])])
        rows.append(["", "", "Total equity", money(data["equity_total_cents"])])
        rows.append(["", "", "Total liabilities + equity",
                     money(data["liabilities_total_cents"] + data["equity_total_cents"])])
        return header, rows
    if kind == "cash-flow":
        header = ["Activity", "Item", "Amount"]
        rows = [[s["name_en"], l["name_en"] or l["name_ar"], money(l["amount_cents"])]
                for s in data["sections"] for l in s["lines"]]
        for s in data["sections"]:
            rows.append([s["name_en"], "Net " + s["name_en"], money(s["total_cents"])])
        rows.append(["", "Opening cash", money(data["opening_cents"])])
        rows.append(["", "Net change", money(data["net_cents"])])
        rows.append(["", "Closing cash", money(data["closing_cents"])])
        return header, rows
    if kind == "equity-changes":
        header = ["Code", "Item", "Opening", "Additions", "Reductions", "Closing"]
        rows = [[r["code"], r["name_ar"], money(r["opening_cents"]), money(r["additions_cents"]),
                 money(r["reductions_cents"]), money(r["closing_cents"])] for r in data["rows"]]
        return header, rows
    if kind == "trial-balance":
        header = ["Code", "Account", "Opening", "Debit", "Credit", "Closing"]
        rows = [[r["code"], r["name_ar"], money(r["opening"]), money(r["period_debit"]),
                 money(r["period_credit"]), money(r["closing"])] for r in data["rows"]]
        return header, rows
    if kind == "general-ledger":
        header = ["Account", "Date", "Entry", "Description", "Debit", "Credit", "Balance"]
        rows = []
        for acc in data["accounts"]:
            rows.append([f'{acc["code"]} — {acc["name_ar"]}', "", "", "Opening balance",
                         0, 0, money(acc["opening_cents"])])
            for l in acc["lines"]:
                rows.append([f'{acc["code"]} — {acc["name_ar"]}', l["entry_date"], l["entry_no"],
                             l.get("description") or "", money(l["debit_cents"]),
                             money(l["credit_cents"]), money(l["running_cents"])])
        return header, rows
    if kind == "journal":
        header = ["Entry", "Date", "Memo", "Account", "Debit", "Credit"]
        rows = []
        for e in data["entries"]:
            for l in e["lines"]:
                rows.append([e["entry_no"], e["entry_date"], e["memo"],
                             f'{l["code"]} — {l["name_ar"]}',
                             money(l["debit_cents"]), money(l["credit_cents"])])
        return header, rows
    if kind in ("receivables-aging", "payables-aging"):
        header = ["Invoice", "Party", "Date", "Due", "Total", "Remaining", "Days late", "Bucket"]
        rows = [[i["invoice_no"], i["party_ar"], i["invoice_date"], i["due_date"],
                 money(i["total_cents"]), money(i["remaining_cents"]), i["days_overdue"], i["bucket"]]
                for i in data["items"]]
        return header, rows
    if kind == "debt-position":
        header = ["Code", "Account", "Unit", "Due", "Paid", "Net"]
        rows = [[a["code"], a["name_ar"], a["unit"], money(a["due_cents"]),
                 money(a["paid_cents"]), money(a["net_cents"])] for a in data["accounts"]]
        return header, rows
    raise ApiError("INVALID_INPUT: نوع القائمة غير مدعوم / Unsupported statement")


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
    problems = password_problems(pwd, b["username"], f'{b.get("full_name_en", "")} {b.get("full_name_ar", "")}')
    if problems:
        raise ApiError("WEAK_PASSWORD: كلمة المرور ضعيفة / Password is too weak",
                       400, {"rules": problems})
    cur = ctx.conn.execute("""INSERT INTO users (company_id, branch_id, username, full_name_ar, full_name_en, email,
        password_hash, role_code, language, theme, password_must_change)
        VALUES (?,?,?,?,?,?,?,?,'ar','dark',1)""",
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
# The assistant answers every part of the system it is allowed to see. Two
# layers:
#
#   1. An intent router with ~20 topics, ordered by specificity, each topic
#      producing a *structured* answer (headline + bullet evidence + numbers),
#      so the same payload can be rendered as text, lists or charts.
#   2. A "deep" pass that adds period comparison, ratios and a next-step hint.
#
# Every figure is computed from the ledger through reports.py at the moment of
# the question — no cached numbers, no external model, no SQL from the client.
# Everything is permission-checked: a question about payroll is refused to a
# user without ``payroll.view`` even though the assistant could compute it.

ASSISTANT_TOPICS = [
    "summary", "profit", "revenue", "expenses", "cash", "receivables", "payables",
    "inventory", "sales", "purchases", "payroll", "assets", "budgets", "debt",
    "allowance", "banks", "journals", "periods", "vat", "kpi", "statement",
    "users", "audit", "help",
]

_TOPIC_PERMISSION = {
    "summary": "dashboard.view", "profit": "reports.view", "revenue": "reports.view",
    "expenses": "reports.view", "cash": "reports.view", "receivables": "customers.view",
    "payables": "payables.view", "inventory": "inventory.view", "sales": "sales.view",
    "purchases": "purchases.view", "payroll": "payroll.view", "assets": "assets.view",
    "budgets": "budgets.view", "debt": "debt.view", "allowance": "allowance.view",
    "banks": "banks.view", "journals": "journal.view", "periods": "reports.view",
    "vat": "reports.view", "kpi": "dashboard.view", "statement": "reports.view",
    "users": "users.manage", "audit": "audit.view", "help": "ai.use",
}


def _money_q(cents):
    try:
        return f"{money_str(int(cents))} ج.م"
    except Exception:
        return "0.00 ج.م"


def _pct(part, whole):
    try:
        return round(part / whole * 100, 1) if whole else 0
    except Exception:
        return 0


def _assistant_intent(qn: str) -> str:
    """Ordered intent router. The first rule that fires wins, so the most
    specific vocabulary is tested first ("VAT" before "revenue")."""
    q = qn
    rules = [
        ("vat", ("ضريبة", "قيمة مضافة", "vat", "tax", "ضرائب")),
        ("audit", ("تدقيق", "audit", "سجل", "log", "من عمل")),
        ("users", ("مستخدم", "مستخدمين", "صلاحي", "user", "permission", "role", "دور")),
        ("periods", ("فترة", "فترات", "إقفال", "اقفال", "period", "close", "fiscal")),
        ("statement", ("قائمة", "قوائم", "ميزانية", "مركز مالى", "مركز مالي", "المركز المالي",
                       "statement", "balance sheet", "financial statement", "قائمة الدخل",
                       "income statement", "تدفق", "cash flow", "قوائم مالية")),
        ("journals", ("قيد", "قيود", "journal", "entry", "ترحيل", "posting")),
        ("allowance", ("إعاشة", "اعاشة", "allowance", "subsistence", "كشف العمال")),
        ("payroll", ("راتب", "رواتب", "مرتب", "payroll", "موظف", "موظفين", "employee", "أجر", "اجر")),
        ("assets", ("أصل", "أصول", "asset", "إهلاك", "اهلاك", "depreciation")),
        ("inventory", ("مخزون", "صنف", "أصناف", "inventory", "stock", "warehouse")),
        ("budgets", ("موازنة", "موازنات", "budget", "انحراف", "variance")),
        ("banks", ("بنك", "بنوك", "صندوق", "خزينة", "bank", "reconcil", "تسوية")),
        ("cash", ("سيولة", "نقدية", "cash", "liquidity", "نقد")),
        ("receivables", ("عميل", "عملاء", "مدين", "تحصيل", "customer", "receivable",
                         "aging", "أعمار", "اعمار", "overdue", "متأخر")),
        ("payables", ("مورد", "موردين", "دائن", "vendor", "supplier", "payable")),
        ("debt", ("مديونية", "مقاول", "contractor", "debt", "كشف حساب")),
        ("purchases", ("مشتريات", "شراء", "purchase")),
        ("sales", ("مبيعات", "بيع", "sales", "إيراد", "ايراد", "revenue", "فواتير")),
        ("expenses", ("مصروف", "مصروفات", "تكلفة", "expense", "cost")),
        ("profit", ("ربح", "أرباح", "خسارة", "profit", "loss", "صافي", "هامش", "margin")),
        ("kpi", ("kpi", "مؤشر", "مؤشرات", "نسبة التداول", "ratio")),
        ("summary", ("ملخص", "summary", "أداء", "performance", "نظرة", "overview",
                     "لوحة", "dashboard", "كيف حال")),
        ("revenue", ("دخل", "income")),
    ]
    for topic, words in rules:
        if any(w in q for w in words):
            return topic
    return "help"


def _assistant_answer(ctx, question: str):
    cid = ensure_company(ctx.user)
    conn = ctx.conn
    as_of = date.today().isoformat()
    fy_start = f"{date.today().year}-01-01"
    qn = question.lower()
    topic = _assistant_intent(qn)

    perm = _TOPIC_PERMISSION.get(topic, "ai.use")
    if not has(ctx.user, perm):
        return {
            "answer": ("لا أملك صلاحية الوصول إلى هذا الجزء من النظام بناءً على دورك الحالي. "
                       "اطلب من مدير النظام منحك الصلاحية إن كنت تحتاجها.\n"
                       "You do not have permission for this part of the system — ask your administrator."),
            "source": "permission", "topic": topic, "kind": "denied",
            "cards": [], "suggestions": [], "refs": [],
        }

    def with_meta(payload, cards=None, refs=None, suggestions=None, kind="text"):
        payload.setdefault("topic", topic)
        payload.setdefault("kind", kind)
        payload.setdefault("cards", cards or [])
        payload.setdefault("refs", refs or [])
        payload.setdefault("suggestions", suggestions or [])
        payload.setdefault("as_of", as_of)
        return payload

    def ref(label_ar, label_en, hash_path, kind="statement"):
        return {"label_ar": label_ar, "label_en": label_en, "hash": hash_path, "kind": kind}

    # ------------------------------------------------------------------
    if topic == "summary":
        d = reports.dashboard(conn, cid, as_of)
        k = d["kpis"]
        inc = reports.income_statement(conn, cid, fy_start, as_of)
        prev_start = f"{date.today().year - 1}-01-01"
        prev_end = f"{date.today().year - 1}-12-31"
        prev = reports.income_statement(conn, cid, prev_start, prev_end)
        growth = _pct(inc["total_revenue_cents"] - prev["total_revenue_cents"],
                      prev["total_revenue_cents"]) if prev["total_revenue_cents"] else None
        lines = [
            f"• الإيرادات: {_money_q(k['revenue_cents'])}",
            f"• المصروفات: {_money_q(k['expenses_cents'])}",
            f"• صافي الربح: {_money_q(k['net_profit_cents'])} (هامش {_pct(k['net_profit_cents'], k['revenue_cents'] or 1)}%)",
            f"• السيولة: {_money_q(k['cash_cents'])}",
            f"• مستحقات العملاء: {_money_q(k['ar_cents'])} منها {_money_q(k['overdue_ar_cents'])} متأخرة",
            f"• مديونية الموردين: {_money_q(k['ap_cents'])} منها {_money_q(k['vendor_overdue_cents'])} متأخرة",
            f"• المخزون: {_money_q(k['inventory_value_cents'])}",
            f"• اعتمادات معلّقة: {k['pending_approvals']}",
        ]
        if growth is not None:
            lines.append(f"• نمو الإيرادات عن العام الماضي: {growth}%")
        answer = ("ملخص الأداء حتى " + as_of + ":\n" + "\n".join(lines) +
                  "\nالمصدر: لوحة التحكم + قائمة الدخل (قيود مرحّلة).")
        return with_meta(
            {"answer": answer, "source": "dashboard", "data": k,
             "headline": f"صافي الربح {_money_q(k['net_profit_cents'])} والسيولة {_money_q(k['cash_cents'])}"},
            cards=[{"label_ar": "الإيرادات", "label_en": "Revenue", "value": k["revenue_cents"], "tone": "ok"},
                   {"label_ar": "المصروفات", "label_en": "Expenses", "value": k["expenses_cents"], "tone": "danger"},
                   {"label_ar": "صافي الربح", "label_en": "Net profit", "value": k["net_profit_cents"],
                    "tone": "ok" if k["net_profit_cents"] >= 0 else "danger"},
                   {"label_ar": "السيولة", "label_en": "Cash", "value": k["cash_cents"]},
                   {"label_ar": "مستحقات العملاء", "label_en": "Receivables", "value": k["ar_cents"], "tone": "warn"},
                   {"label_ar": "مديونية الموردين", "label_en": "Payables", "value": k["ap_cents"], "tone": "warn"}],
            refs=[ref("لوحة التحكم", "Dashboard", "#/dashboard", "page"),
                  ref("قائمة الدخل", "Income statement", "#/statements/income-statement")],
            suggestions=["ما أكبر بنود المصروفات؟", "أعمار ديون العملاء؟", "ما هي السيولة المتاحة؟"],
            kind="summary")

    # ------------------------------------------------------------------
    if topic in ("profit", "revenue"):
        inc = reports.income_statement_full(conn, cid, fy_start, as_of)
        if topic == "profit":
            answer = (f"من {fy_start} حتى {as_of}:\n"
                      f"• صافي الإيرادات: {_money_q(inc['total_revenue_cents'])}\n"
                      f"• تكلفة النشاط: {_money_q(inc['sections']['cost_of_sales']['total_cents'])}\n"
                      f"• الربح الإجمالي: {_money_q(inc['gross_profit_cents'])} (هامش {inc['gross_margin_pct']}%)\n"
                      f"• المصروفات التشغيلية: {_money_q(inc['operating_expenses_cents'])}\n"
                      f"• الربح التشغيلي: {_money_q(inc['operating_profit_cents'])}\n"
                      f"• صافي الربح: {_money_q(inc['net_profit_cents'])} (هامش {inc['net_margin_pct']}%)\n"
                      f"المصدر: قائمة الدخل متعددة الخطوات — قيود مرحّلة فقط.")
        else:
            top = sorted(inc["sections"]["revenue"]["lines"], key=lambda l: -l["amount_cents"])[:6]
            lines = "\n".join(f"• {l['name_ar']} ({l['code']}): {_money_q(l['amount_cents'])}" for l in top)
            answer = (f"إيرادات الفترة {fy_start} → {as_of}: {_money_q(inc['total_revenue_cents'])}.\n"
                      f"أكبر مصادر الإيراد:\n{lines}\n"
                      f"المصدر: قائمة الدخل.")
        return with_meta(
            {"answer": answer, "source": "income_statement",
             "data": {"net_profit_cents": inc["net_profit_cents"],
                      "revenue_cents": inc["total_revenue_cents"],
                      "gross_profit_cents": inc["gross_profit_cents"],
                      "operating_cents": inc["operating_profit_cents"]},
             "headline": f"صافي الربح {_money_q(inc['net_profit_cents'])} على إيرادات {_money_q(inc['total_revenue_cents'])}"},
            cards=[{"label_ar": "صافي الإيرادات", "label_en": "Net revenue", "value": inc["total_revenue_cents"], "tone": "ok"},
                   {"label_ar": "الربح الإجمالي", "label_en": "Gross profit", "value": inc["gross_profit_cents"],
                    "tone": "ok" if inc["gross_profit_cents"] >= 0 else "danger"},
                   {"label_ar": "الربح التشغيلي", "label_en": "Operating profit", "value": inc["operating_profit_cents"],
                    "tone": "ok" if inc["operating_profit_cents"] >= 0 else "danger"},
                   {"label_ar": "صافي الربح", "label_en": "Net profit", "value": inc["net_profit_cents"],
                    "tone": "ok" if inc["net_profit_cents"] >= 0 else "danger"},
                   {"label_ar": "هامش الربح الإجمالي", "label_en": "Gross margin", "value": f"{inc['gross_margin_pct']}%", "raw": True},
                   {"label_ar": "هامش الربح الصافي", "label_en": "Net margin", "value": f"{inc['net_margin_pct']}%", "raw": True}],
            refs=[ref("قائمة الدخل", "Income statement", "#/statements/income-statement")],
            suggestions=["قارن بالمصروفات", "ما هي السيولة؟", "أعمار ديون العملاء؟"],
            kind="statement")

    # ------------------------------------------------------------------
    if topic == "expenses":
        inc = reports.income_statement_full(conn, cid, fy_start, as_of)
        all_rows = []
        for sec in inc["sections"]["operating_expenses"]:
            all_rows += sec["lines"]
        all_rows += inc["sections"]["cost_of_sales"]["lines"]
        rows = sorted(all_rows, key=lambda l: -l["amount_cents"])[:8]
        lines = "\n".join(f"• {r['name_ar']} ({r['code']}): {_money_q(r['amount_cents'])} "
                          f"— {_pct(r['amount_cents'], inc['total_expenses_cents'] or 1)}% من المصروفات"
                          for r in rows)
        answer = (f"إجمالي المصروفات (تكلفة + تشغيل) من {fy_start} حتى {as_of}: "
                  f"{_money_q(inc['total_expenses_cents'])}.\nأكبر البنود:\n{lines}\n"
                  f"نسبة إجمالي المصروفات إلى الإيرادات: {_pct(inc['total_expenses_cents'], inc['total_revenue_cents'] or 1)}%.\n"
                  f"المصدر: قائمة الدخل.")
        # `data` stays the line-item list (that is what the UI tables want), so the
        # revenue figure the English headline needs rides on the payload itself.
        return with_meta({"answer": answer, "source": "income_statement", "data": rows,
                          "revenue_cents": inc["total_revenue_cents"],
                          "expenses_cents": inc["total_expenses_cents"],
                          "headline": f"المصروفات {_money_q(inc['total_expenses_cents'])} بنسبة {inc['expense_ratio_pct']}% من الإيرادات"},
                         cards=[{"label_ar": "تكلفة النشاط", "label_en": "Cost of sales",
                                 "value": inc["sections"]["cost_of_sales"]["total_cents"], "tone": "danger"},
                                {"label_ar": "مصروفات تشغيلية", "label_en": "Operating expenses",
                                 "value": inc["operating_expenses_cents"], "tone": "warn"},
                                {"label_ar": "الإهلاك", "label_en": "Depreciation", "value": inc["depreciation_cents"]}],
                         refs=[ref("قائمة الدخل", "Income statement", "#/statements/income-statement")],
                         suggestions=["ما صافي الربح؟", "الموازنة مقابل الفعلي؟"], kind="list")

    # ------------------------------------------------------------------
    if topic == "cash" or topic == "banks":
        cash = reports.cash_position(conn, cid)
        details = "\n".join(f"• {a['name_ar']}: {_money_q(a['balance_cents'])}" for a in cash["accounts"])
        cf = reports.cash_flow_statement(conn, cid, fy_start, as_of)
        answer = (f"السيولة الحالية {_money_q(cash['total_cents'])} موزعة كالتالي:\n{details}\n\n"
                  f"حركة النقدية من {fy_start} إلى {as_of}:\n"
                  f"• تشغيلية: {_money_q(cf['activities']['operating'])}\n"
                  f"• استثمارية: {_money_q(cf['activities']['investing'])}\n"
                  f"• تمويلية: {_money_q(cf['activities']['financing'])}\n"
                  f"• صافي التغير: {_money_q(cf['net_cents'])} (من {_money_q(cf['opening_cents'])} إلى {_money_q(cf['closing_cents'])})\n"
                  f"المصدر: أرصدة البنوك والصناديق + قائمة التدفقات النقدية.")
        return with_meta({"answer": answer, "source": "cash_position",
                          "data": {"accounts": cash["accounts"], "total_cents": cash["total_cents"],
                                   "flow": cf["activities"]},
                          "headline": f"السيولة {_money_q(cash['total_cents'])} وصافي التغير النقدي {_money_q(cf['net_cents'])}"},
                         cards=[{"label_ar": "إجمالي السيولة", "label_en": "Total cash", "value": cash["total_cents"], "tone": "ok"}]
                         + [{"label_ar": a["name_ar"], "label_en": a.get("name_en") or a["name_ar"],
                             "value": a["balance_cents"],
                             "tone": "danger" if a["balance_cents"] < 0 else ""} for a in cash["accounts"][:4]],
                         refs=[ref("البنوك والصناديق", "Banks & cash", "#/banks", "page"),
                               ref("قائمة التدفقات النقدية", "Cash flow", "#/statements/cash-flow")],
                         suggestions=["أعمار ديون العملاء؟", "مديونية الموردين؟"], kind="cards")

    # ------------------------------------------------------------------
    if topic == "receivables":
        ar = reports.aging(conn, cid, as_of, "sales")
        pos = reports.ar_position(conn, cid)
        top = [c for c in pos["customers"] if c["outstanding_cents"]][:5]
        lines = "\n".join(f"• {c['name_ar']}: {_money_q(c['outstanding_cents'])}" for c in top)
        b = ar["buckets"]
        answer = (f"مستحقات العملاء حتى {as_of}: {_money_q(pos['total_cents'])}.\n"
                  f"أعمار الديون: جاري {_money_q(b['current'])} · 1–30 يوم {_money_q(b['1_30'])} · "
                  f"31–60 {_money_q(b['31_60'])} · 61–90 {_money_q(b['61_90'])} · أكثر من 90 {_money_q(b['over_90'])}\n"
                  f"أكبر الأرصدة:\n{lines}\nالمصدر: أرصدة العملاء وتقرير أعمار الديون.")
        return with_meta({"answer": answer, "source": "aging", "data": {"buckets": b, "customers": top},
                          "headline": f"مستحقات العملاء {_money_q(pos['total_cents'])}، منها {_money_q(b['over_90'])} فوق 90 يومًا"},
                         cards=[{"label_ar": "إجمالي المستحق", "label_en": "Outstanding", "value": pos["total_cents"], "tone": "warn"},
                                {"label_ar": "متأخر +90 يوم", "label_en": "Over 90 days", "value": b["over_90"], "tone": "danger"},
                                {"label_ar": "61–90 يوم", "label_en": "61–90 days", "value": b["61_90"], "tone": "warn"},
                                {"label_ar": "31–60 يوم", "label_en": "31–60 days", "value": b["31_60"]},
                                {"label_ar": "1–30 يوم", "label_en": "1–30 days", "value": b["1_30"]},
                                {"label_ar": "غير مستحق", "label_en": "Current", "value": b["current"], "tone": "ok"}],
                         refs=[ref("أعمار ديون العملاء", "Receivables aging", "#/statements/receivables-aging"),
                               ref("العملاء", "Customers", "#/customers", "page")],
                         suggestions=["مديونية الموردين؟", "السيولة المتاحة؟"], kind="cards")

    # ------------------------------------------------------------------
    if topic == "payables":
        ap = reports.vendor_payables(conn, cid, as_of)
        top = sorted(ap["vendors"], key=lambda v: -v["outstanding_cents"])[:5]
        lines = "\n".join(f"• {v['name_ar']} — {_money_q(v['outstanding_cents'])} "
                          f"(متأخر: {_money_q(v['overdue_cents'])})" for v in top if v["outstanding_cents"])
        b = ap["aging"]
        answer = (f"إجمالي مديونية الموردين حتى {as_of}: {_money_q(ap['totals']['outstanding_cents'])}، "
                  f"منها {_money_q(ap['totals']['overdue_cents'])} متأخرة.\n"
                  f"أعمار: جاري {_money_q(b['current'])} · 1–30 {_money_q(b['1_30'])} · "
                  f"31–60 {_money_q(b['31_60'])} · 61–90 {_money_q(b['61_90'])} · +90 {_money_q(b['over_90'])}\n"
                  f"أعلى الأرصدة:\n{lines}\n"
                  f"رصيد حساب الموردين في الأستاذ: {_money_q(ap['totals']['journal_balance_cents'])}.\n"
                  f"المصدر: مديونية الموردين (فواتير مرحّلة + الأستاذ).")
        return with_meta({"answer": answer, "source": "vendor_payables",
                          "data": {"total_cents": ap["totals"]["outstanding_cents"], "top": top, "aging": b},
                          "headline": f"مديونية الموردين {_money_q(ap['totals']['outstanding_cents'])} منها {_money_q(ap['totals']['overdue_cents'])} متأخرة"},
                         cards=[{"label_ar": "إجمالي المستحق", "label_en": "Outstanding", "value": ap["totals"]["outstanding_cents"], "tone": "warn"},
                                {"label_ar": "المتأخر", "label_en": "Overdue", "value": ap["totals"]["overdue_cents"], "tone": "danger"},
                                {"label_ar": "المسدد", "label_en": "Paid", "value": ap["totals"]["paid_cents"], "tone": "ok"},
                                {"label_ar": "رصيد الأستاذ", "label_en": "GL balance", "value": ap["totals"]["journal_balance_cents"]}],
                         refs=[ref("مديونية الموردين", "Vendor payables", "#/payables", "page"),
                               ref("أعمار ديون الموردين", "Payables aging", "#/statements/payables-aging")],
                         suggestions=["أعمار ديون العملاء؟", "السيولة المتاحة؟"], kind="cards")

    # ------------------------------------------------------------------
    if topic == "inventory":
        rows = conn.execute(
            """SELECT p.sku, p.name_ar, p.name_en, p.unit,
                      COALESCE(SUM(s.qty_milli),0) qty, COALESCE(SUM(s.value_cents),0) value
               FROM products p LEFT JOIN stock_movements s ON s.product_id=p.id AND s.company_id=p.company_id
               WHERE p.company_id=? GROUP BY p.id ORDER BY value DESC""", (cid,)).fetchall()
        total = sum(r["value"] for r in rows)
        lines = "\n".join(f"• {r['name_ar']} ({r['sku']}): {r['qty'] / 1000:g} {r['unit']} — {_money_q(r['value'])}"
                          for r in rows[:8])
        answer = (f"قيمة المخزون الحالية {_money_q(total)} على {len(rows)} صنف.\n{lines}\n"
                  f"المصدر: حركات المخزون (تقوم المتوسط المرجح).")
        return with_meta({"answer": answer, "source": "inventory",
                          "data": {"total_cents": total, "products": [dict(r) for r in rows[:10]]},
                          "headline": f"قيمة المخزون {_money_q(total)}"},
                         cards=[{"label_ar": "قيمة المخزون", "label_en": "Inventory value", "value": total}]
                         + [{"label_ar": r["name_ar"], "label_en": r["name_en"] or r["name_ar"],
                             "value": r["value"]} for r in rows[:3]],
                         refs=[ref("المخزون", "Inventory", "#/inventory", "page")],
                         suggestions=["ما أكبر بنود المصروفات؟"], kind="cards")

    # ------------------------------------------------------------------
    if topic == "sales" or topic == "purchases":
        table = "sales_invoices" if topic == "sales" else "purchase_invoices"
        party = "customers" if topic == "sales" else "vendors"
        col = "customer_id" if topic == "sales" else "vendor_id"
        rows = conn.execute(
            f"""SELECT i.invoice_no, i.invoice_date, i.total_cents, i.paid_cents, i.status,
                       p.name_ar FROM {table} i JOIN {party} p ON p.id=i.{col}
                WHERE i.company_id=? ORDER BY i.invoice_date DESC LIMIT 10""", (cid,)).fetchall()
        agg = conn.execute(
            f"""SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) t, COALESCE(SUM(paid_cents),0) p
                FROM {table} WHERE company_id=? AND status IN ('posted','partially_paid','paid')""",
            (cid,)).fetchone()
        lines = "\n".join(f"• {r['invoice_no']} — {r['name_ar']} — {_money_q(r['total_cents'])} ({r['status']})"
                          for r in rows[:6])
        label = "المبيعات" if topic == "sales" else "المشتريات"
        answer = (f"{label}: {agg['n']} فاتورة مرحّلة بإجمالي {_money_q(agg['t'])}، "
                  f"المحصّل/المدفوع {_money_q(agg['p'])}.\nأحدث الفواتير:\n{lines}\n"
                  f"المصدر: سجل فواتير {'البيع' if topic == 'sales' else 'الشراء'}.")
        return with_meta({"answer": answer, "source": table,
                          "data": {"count": agg["n"], "total_cents": agg["t"], "paid_cents": agg["p"]},
                          "headline": f"{label} {_money_q(agg['t'])} في {agg['n']} فاتورة"},
                         cards=[{"label_ar": "إجمالي الفواتير", "label_en": "Invoiced", "value": agg["t"]},
                                {"label_ar": "المسدد", "label_en": "Paid", "value": agg["p"], "tone": "ok"},
                                {"label_ar": "المتبقي", "label_en": "Outstanding", "value": agg["t"] - agg["p"], "tone": "warn"},
                                {"label_ar": "عدد الفواتير", "label_en": "Invoices", "value": agg["n"], "raw": True}],
                         refs=[ref(label, "Sales" if topic == "sales" else "Purchases",
                                   "#/sales" if topic == "sales" else "#/purchases", "page")],
                         kind="cards")

    # ------------------------------------------------------------------
    if topic == "payroll":
        runs = conn.execute(
            """SELECT run_no, period, gross_cents, deductions_cents, net_cents, status
               FROM payroll_runs WHERE company_id=? ORDER BY period DESC LIMIT 6""", (cid,)).fetchall()
        emps = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(basic_salary_cents),0) s FROM employees "
                            "WHERE company_id=? AND is_active=1", (cid,)).fetchone()
        lines = "\n".join(f"• {r['period']}: صافي {_money_q(r['net_cents'])} ({r['status']})" for r in runs)
        answer = (f"الرواتب: {emps['n']} موظف نشط بإجمالي أساسي {_money_q(emps['s'])} شهريًا.\n"
                  f"آخر مسيرات الرواتب:\n{lines or '• لا توجد مسيرات بعد.'}\n"
                  f"المصدر: مسيرات الرواتب والموظفون.")
        return with_meta({"answer": answer, "source": "payroll",
                          "data": {"employees": emps["n"], "runs": [dict(r) for r in runs]},
                          "headline": f"{emps['n']} موظف نشط — إجمالي أساسي {_money_q(emps['s'])}"},
                         cards=[{"label_ar": "موظفون نشطون", "label_en": "Active employees", "value": emps["n"], "raw": True},
                                {"label_ar": "إجمالي الأساسي", "label_en": "Basic payroll", "value": emps["s"]}]
                         + [{"label_ar": f"صافي {r['period']}", "label_en": r["period"], "value": r["net_cents"]}
                            for r in runs[:3]],
                         refs=[ref("الرواتب", "Payroll", "#/payroll", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "assets":
        rows = conn.execute(
            """SELECT code, name_ar, cost_cents, accumulated_cents, useful_life_months, status
               FROM fixed_assets WHERE company_id=? ORDER BY code""", (cid,)).fetchall()
        cost = sum(r["cost_cents"] for r in rows)
        acc = sum(r["accumulated_cents"] for r in rows)
        lines = "\n".join(f"• {r['name_ar']} ({r['code']}): تكلفة {_money_q(r['cost_cents'])} — "
                          f"مجمع إهلاك {_money_q(r['accumulated_cents'])}" for r in rows[:8])
        answer = (f"الأصول الثابتة: {len(rows)} أصل بتكلفة {_money_q(cost)}، "
                  f"مجمع الإهلاك {_money_q(acc)}، القيمة الدفترية {_money_q(cost - acc)}.\n{lines}\n"
                  f"المصدر: سجل الأصول الثابتة.")
        return with_meta({"answer": answer, "source": "fixed_assets",
                          "data": {"cost_cents": cost, "accumulated_cents": acc},
                          "headline": f"القيمة الدفترية للأصول {_money_q(cost - acc)}"},
                         cards=[{"label_ar": "تكلفة الأصول", "label_en": "Cost", "value": cost},
                                {"label_ar": "مجمع الإهلاك", "label_en": "Accumulated", "value": acc, "tone": "warn"},
                                {"label_ar": "القيمة الدفترية", "label_en": "Book value", "value": cost - acc, "tone": "ok"}],
                         refs=[ref("الأصول", "Assets", "#/assets", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "budgets":
        fy = q1(conn, "SELECT id, name FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?",
                (cid, as_of, as_of))
        rows = reports.budget_vs_actual(conn, cid, fy["id"]) if fy else []
        over = sorted([r for r in rows if r["variance_cents"] < 0], key=lambda r: r["variance_cents"])[:5]
        budget_total = sum(r["budget_cents"] for r in rows)
        actual_total = sum(r["actual_cents"] for r in rows)
        lines = "\n".join(f"• {r['name_ar']}: موازنة {_money_q(r['budget_cents'])} / فعلي "
                          f"{_money_q(r['actual_cents'])} (انحراف {_money_q(r['variance_cents'])})" for r in over)
        answer = (f"الموازنة مقابل الفعلي للسنة {as_of[:4]}: موازنة {_money_q(budget_total)} "
                  f"مقابل فعلي {_money_q(actual_total)}.\n"
                  f"{'أكبر تجاوزات:' if over else 'لا توجد تجاوزات مسجلة.'}\n{lines}\n"
                  f"المصدر: تقرير الموازنة مقابل الفعلي.")
        return with_meta({"answer": answer, "source": "budget_variance",
                          "data": {"rows": rows, "over": over},
                          "headline": f"موازنة {_money_q(budget_total)} مقابل فعلي {_money_q(actual_total)}"},
                         cards=[{"label_ar": "الموازنة", "label_en": "Budget", "value": budget_total},
                                {"label_ar": "الفعلي", "label_en": "Actual", "value": actual_total,
                                 "tone": "danger" if actual_total > budget_total else "ok"},
                                {"label_ar": "الانحراف", "label_en": "Variance", "value": budget_total - actual_total,
                                 "tone": "ok" if budget_total >= actual_total else "danger"}],
                         refs=[ref("الموازنات", "Budgets", "#/budgets", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "debt":
        data = reports.debt_position(conn, cid, fy_start, as_of)
        top = sorted(data["accounts"], key=lambda a: -a["net_cents"])[:6]
        lines = "\n".join(f"• {a['name_ar']}: مستحق {_money_q(a['due_cents'])} — "
                          f"مسدد {_money_q(a['paid_cents'])} — صافي {_money_q(a['net_cents'])}" for a in top)
        answer = (f"مديونية المقاولين والموردين من {fy_start} إلى {as_of}:\n"
                  f"• إجمالي المستحق: {_money_q(data['totals']['due_cents'])}\n"
                  f"• إجمالي المسدد: {_money_q(data['totals']['paid_cents'])}\n"
                  f"• الصافي: {_money_q(data['totals']['net_cents'])}\n{lines}\n"
                  f"المصدر: حساب المديونية وكشوف المقاولين.")
        return with_meta({"answer": answer, "source": "debt_position", "data": data,
                          "headline": f"صافي مديونية المقاولين {_money_q(data['totals']['net_cents'])}"},
                         cards=[{"label_ar": "المستحق", "label_en": "Due", "value": data["totals"]["due_cents"], "tone": "warn"},
                                {"label_ar": "المسدد", "label_en": "Paid", "value": data["totals"]["paid_cents"], "tone": "ok"},
                                {"label_ar": "الصافي", "label_en": "Net", "value": data["totals"]["net_cents"], "tone": "danger"}],
                         refs=[ref("المديونية", "Debt", "#/debt", "page"),
                               ref("حساب المديونية", "Debt position", "#/statements/debt-position")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "allowance":
        row = conn.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) total, COALESCE(SUM(people_count),0) people
               FROM allowance_periods WHERE company_id=? AND start_date>=? AND start_date<=?""",
            (cid, fy_start, as_of)).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) n FROM allowance_periods WHERE company_id=? AND status<>'posted'",
            (cid,)).fetchone()["n"]
        summary = {"periods": row["n"], "total_cents": row["total"], "people": row["people"],
                   "pending": pending}
        answer = (f"إعاشة العاملين من {fy_start} إلى {as_of}: {row['n']} كشف، "
                  f"إجمالي {_money_q(row['total'])}، وعناصر مرتبطة {row['people']}.\n"
                  f"كشوف لم تُرحّل بعد: {pending}.\n"
                  f"المصدر: كشوف الإعاشة.")
        return with_meta({"answer": answer, "source": "allowance", "data": summary,
                          "headline": f"إعاشة {_money_q(row['total'])} في {row['n']} كشف"},
                         cards=[{"label_ar": "إجمالي الإعاشة", "label_en": "Total", "value": row["total"]},
                                {"label_ar": "عدد الكشوف", "label_en": "Sheets", "value": row["n"], "raw": True},
                                {"label_ar": "غير مرحّلة", "label_en": "Not posted", "value": pending, "raw": True,
                                 "tone": "warn" if pending else "ok"}],
                         refs=[ref("إعاشة العاملين", "Allowance", "#/allowance", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "journals":
        rows = conn.execute(
            """SELECT e.entry_no, e.entry_date, e.memo, e.status, e.total_debit_cents
               FROM journal_entries e WHERE e.company_id=? ORDER BY e.id DESC LIMIT 8""", (cid,)).fetchall()
        counts = conn.execute(
            """SELECT status, COUNT(*) n FROM journal_entries WHERE company_id=? GROUP BY status""",
            (cid,)).fetchall()
        by_status = {r["status"]: r["n"] for r in counts}
        lines = "\n".join(f"• {r['entry_no']} — {r['entry_date']} — {r['memo'][:40]} "
                          f"({_money_q(r['total_debit_cents'])})" for r in rows[:6])
        answer = ("حالة القيود: " + " · ".join(f"{k}: {v}" for k, v in by_status.items()) + "\n"
                  f"أحدث القيود:\n{lines}\nالمصدر: دفتر اليومية.")
        return with_meta({"answer": answer, "source": "journal",
                          "data": {"by_status": by_status},
                          "headline": f"{by_status.get('posted', 0)} قيد مرحّل و{by_status.get('draft', 0)} مسودة"},
                         cards=[{"label_ar": f"قيود {k}", "label_en": k, "value": v, "raw": True}
                                for k, v in by_status.items()][:5],
                         refs=[ref("القيود اليومية", "Journal", "#/journal", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "periods":
        rows = conn.execute(
            """SELECT p.period_no, p.start_date, p.end_date, p.status, fy.name
               FROM fiscal_periods p JOIN fiscal_years fy ON fy.id=p.fiscal_year_id
               WHERE p.company_id=? ORDER BY p.start_date DESC LIMIT 6""", (cid,)).fetchall()
        lines = "\n".join(f"• {r['name']} — فترة {r['period_no']}: {r['start_date']} → {r['end_date']} ({r['status']})"
                          for r in rows)
        closed = sum(1 for r in rows if r["status"] == "closed")
        answer = (f"الفترات المالية (آخر 6):\n{lines}\n"
                  f"مقفلة: {closed} من {len(rows)}.\nالمصدر: الفترات المالية.")
        return with_meta({"answer": answer, "source": "fiscal_periods",
                          "data": {"periods": [dict(r) for r in rows]},
                          "headline": f"{closed} فترة مقفلة من {len(rows)}"},
                         cards=[{"label_ar": f"{r['name']} ف{r['period_no']}", "label_en": r["start_date"],
                                 "value": r["status"], "raw": True} for r in rows[:4]],
                         refs=[ref("الفترات المالية", "Fiscal periods", "#/periods", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "vat":
        out_vat = conn.execute(
            """SELECT COALESCE(SUM(l.credit_cents-l.debit_cents),0) v FROM v_posted_lines l
               JOIN accounts a ON a.id=l.account_id WHERE l.company_id=? AND a.code='2210'""",
            (cid,)).fetchone()["v"]
        in_vat = conn.execute(
            """SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) v FROM v_posted_lines l
               JOIN accounts a ON a.id=l.account_id WHERE l.company_id=? AND a.code LIKE '126%'""",
            (cid,)).fetchone()["v"]
        answer = (f"موقف ضريبة القيمة المضافة حتى {as_of}:\n"
                  f"• ضريبة المخرجات (مستحقة للمصلحة): {_money_q(out_vat)}\n"
                  f"• ضريبة المدخلات (قابلة للخصم): {_money_q(in_vat)}\n"
                  f"• الصافي المستحق: {_money_q(out_vat - in_vat)}\n"
                  f"المصدر: أرصدة حسابات الضريبة في الأستاذ العام — وليست إقرارًا ضريبيًا.")
        return with_meta({"answer": answer, "source": "vat",
                          "data": {"output": out_vat, "input": in_vat, "net": out_vat - in_vat},
                          "headline": f"صافي ضريبة القيمة المضافة {_money_q(out_vat - in_vat)}"},
                         cards=[{"label_ar": "ضريبة المخرجات", "label_en": "Output VAT", "value": out_vat, "tone": "warn"},
                                {"label_ar": "ضريبة المدخلات", "label_en": "Input VAT", "value": in_vat, "tone": "ok"},
                                {"label_ar": "الصافي", "label_en": "Net VAT", "value": out_vat - in_vat, "tone": "danger"}],
                         refs=[ref("ميزان المراجعة", "Trial balance", "#/statements/trial-balance")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "statement":
        bs = reports.balance_sheet_full(conn, cid, as_of)
        cf = reports.cash_flow_statement(conn, cid, fy_start, as_of)
        inc = reports.income_statement_full(conn, cid, fy_start, as_of)
        answer = (f"ملخص القوائم المالية حتى {as_of}:\n\n"
                  f"قائمة الدخل ({fy_start} → {as_of}):\n"
                  f"• الإيرادات {_money_q(inc['total_revenue_cents'])} — تكلفة {_money_q(inc['sections']['cost_of_sales']['total_cents'])} — "
                  f"ربح إجمالي {_money_q(inc['gross_profit_cents'])} — صافي {_money_q(inc['net_profit_cents'])}\n\n"
                  f"المركز المالي:\n"
                  f"• الأصول {_money_q(bs['total_assets_cents'])} = التزامات {_money_q(bs['total_liabilities_cents'])} + "
                  f"حقوق ملكية {_money_q(bs['total_equity_cents'])} — "
                  f"{'متوازن' if bs['balanced'] else 'غير متوازن'}\n"
                  f"• رأس المال العامل {_money_q(bs['working_capital_cents'])} — نسبة التداول {bs['current_ratio']}\n\n"
                  f"التدفقات النقدية:\n"
                  f"• تشغيلية {_money_q(cf['activities']['operating'])} — استثمارية {_money_q(cf['activities']['investing'])} — "
                  f"تمويلية {_money_q(cf['activities']['financing'])} — نقدية آخر الفترة {_money_q(cf['closing_cents'])}\n"
                  f"المصدر: القوائم المالية من القيود المرحّلة.")
        return with_meta({"answer": answer, "source": "financial_statements",
                          "data": {"balance_sheet": bs, "cash_flow": cf["activities"], "income": inc["net_profit_cents"]},
                          "headline": f"الأصول {_money_q(bs['total_assets_cents'])} — صافي الربح {_money_q(inc['net_profit_cents'])}"},
                         cards=[{"label_ar": "إجمالي الأصول", "label_en": "Assets", "value": bs["total_assets_cents"]},
                                {"label_ar": "إجمالي الالتزامات", "label_en": "Liabilities", "value": bs["total_liabilities_cents"], "tone": "warn"},
                                {"label_ar": "حقوق الملكية", "label_en": "Equity", "value": bs["total_equity_cents"], "tone": "ok"},
                                {"label_ar": "صافي الربح", "label_en": "Net profit", "value": inc["net_profit_cents"],
                                 "tone": "ok" if inc["net_profit_cents"] >= 0 else "danger"},
                                {"label_ar": "رأس المال العامل", "label_en": "Working capital", "value": bs["working_capital_cents"]},
                                {"label_ar": "نسبة التداول", "label_en": "Current ratio", "value": bs["current_ratio"] or "—", "raw": True}],
                         refs=[ref("قائمة الدخل", "Income statement", "#/statements/income-statement"),
                               ref("المركز المالي", "Balance sheet", "#/statements/balance-sheet"),
                               ref("التدفقات النقدية", "Cash flow", "#/statements/cash-flow")], kind="statement")

    # ------------------------------------------------------------------
    if topic == "kpi":
        d = reports.dashboard(conn, cid, as_of)
        k = d["kpis"]
        bs = reports.balance_sheet_full(conn, cid, as_of)
        answer = (f"مؤشرات الأداء حتى {as_of}:\n"
                  f"• هامش الربح الصافي: {_pct(k['net_profit_cents'], k['revenue_cents'] or 1)}%\n"
                  f"• نسبة التداول: {bs['current_ratio']}\n"
                  f"• الالتزامات ÷ حقوق الملكية: {bs['debt_to_equity']}\n"
                  f"• السيولة: {_money_q(k['cash_cents'])}\n"
                  f"• دورة التحصيل (متوسط): {round((k['ar_cents'] / k['revenue_cents'] * 365), 0) if k['revenue_cents'] else 0} يوم\n"
                  f"• مخاطر التأخير: {_money_q(k['overdue_ar_cents'] + k['vendor_overdue_cents'])}\n"
                  f"المصدر: لوحة التحكم + المركز المالي.")
        return with_meta({"answer": answer, "source": "kpi", "data": k,
                          "headline": f"هامش صافي {_pct(k['net_profit_cents'], k['revenue_cents'] or 1)}% ونسبة تداول {bs['current_ratio']}"},
                         cards=[{"label_ar": "هامش صافي", "label_en": "Net margin",
                                 "value": f"{_pct(k['net_profit_cents'], k['revenue_cents'] or 1)}%", "raw": True},
                                {"label_ar": "نسبة التداول", "label_en": "Current ratio", "value": bs["current_ratio"] or "—", "raw": True},
                                {"label_ar": "الالتزامات/الملكية", "label_en": "D/E", "value": bs["debt_to_equity"] or "—", "raw": True}],
                         refs=[ref("لوحة التحكم", "Dashboard", "#/dashboard", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "users":
        rows = conn.execute("SELECT username, full_name_ar, role_code, is_active FROM users "
                            "WHERE company_id=? OR company_id IS NULL ORDER BY id", (cid,)).fetchall()
        lines = "\n".join(f"• {r['username']} — {r['full_name_ar']} ({r['role_code']})"
                          f"{'' if r['is_active'] else ' — موقوف'}" for r in rows)
        answer = (f"مستخدمو الشركة ({len(rows)}):\n{lines}\n"
                  f"الصلاحيات تُفرض على الخادم في كل طلب.\nالمصدر: إدارة المستخدمين.")
        return with_meta({"answer": answer, "source": "users", "data": {"users": [dict(r) for r in rows]},
                          "headline": f"{len(rows)} مستخدم"},
                         cards=[{"label_ar": "مستخدمون", "label_en": "Users", "value": len(rows), "raw": True}],
                         refs=[ref("المستخدمون", "Users", "#/users", "page")], kind="cards")

    # ------------------------------------------------------------------
    if topic == "audit":
        rows = conn.execute(
            """SELECT username, action, entity, created_at FROM audit_logs
               WHERE company_id=? OR company_id IS NULL ORDER BY id DESC LIMIT 10""", (cid,)).fetchall()
        lines = "\n".join(f"• {r['created_at']} — {r['username']} — {r['action']}" for r in rows)
        answer = f"أحدث أحداث سجل التدقيق:\n{lines}\nالمصدر: سجل التدقيق."
        return with_meta({"answer": answer, "source": "audit_log", "data": {"logs": [dict(r) for r in rows]},
                          "headline": f"آخر حدث: {rows[0]['action'] if rows else '—'}"},
                         cards=[{"label_ar": "أحداث مسجلة", "label_en": "Events", "value": len(rows), "raw": True}],
                         refs=[ref("سجل التدقيق", "Audit log", "#/audit", "page")], kind="cards")

    # ------------------------------------------------------------------
    answer = ("أستطيع الإجابة عن أي جزء من النظام: الأرباح والإيرادات والمصروفات، السيولة والبنوك، "
              "مستحقات العملاء ومديونية الموردين وأعمار الديون، المخزون، المبيعات والمشتريات، الرواتب، "
              "الأصول والإهلاك، الموازنة والانحرافات، المديونية وكشوف المقاولين، الإعاشة، ضريبة القيمة المضافة، "
              "القيود والفترات المالية، المستخدمون وسجل التدقيق، أو أي قائمة من القوائم المالية.\n\n"
              "جرّب مثلًا: «ما صافي الربح هذا العام؟» أو «اعرض لي المركز المالي» أو «أعمار ديون العملاء؟».\n"
              "تنبيه: كل الأرقام من القيود المرحّلة وضمن صلاحياتك فقط، ولا يقوم المساعد بأي ترحيل أو دفع.")
    return with_meta({"answer": answer, "source": "help", "kind": "help",
                      "suggestions": ["ملخص الأداء", "المركز المالي", "أعمار ديون العملاء",
                                      "مديونية الموردين", "موقف ضريبة القيمة المضافة", "الموازنة مقابل الفعلي"]},
                     refs=[ref("القوائم المالية", "Financial statements", "#/statements", "page")],
                     kind="help")


@route("POST", r"/api/assistant/ask", "ai.use")
def assistant(ctx):
    """Permission-scoped finance assistant (deterministic, auditable).

    Every answer is computed live from the ledger; nothing is generated by an
    external model and nothing is written. The payload is structured so the UI
    can render headline + KPI cards + evidence bullets + deep links, which is
    what makes the assistant usable across the whole ERP rather than as a
    single text blob.
    """
    question = (ctx.body.get("question") or "").strip()
    if not question:
        raise ApiError("EMPTY_QUESTION: اكتب سؤالك / Please type a question")
    if len(question) > 500:
        question = question[:500]
    result = _assistant_answer(ctx, question)
    result["question"] = question
    # English renderings are derived from the same payload, so the two languages
    # can never disagree about a number. The Arabic answer stays the payload's
    # primary text; `answer_en` rides alongside it.
    try:
        head_en, body_en = _assistant_english(result)
        if body_en:
            result["answer_en"] = body_en
            if head_en:
                result["headline_en"] = head_en
    except Exception:          # a translation bug must never break the answer
        pass
    if result.get("topic") not in (None, "help", "permission"):
        audit(ctx.conn, ctx.user, "assistant.ask", "assistant", None,
              {"topic": result.get("topic"), "question": question[:160]},
              company_id=ctx.user["company_id"])
    return result


# ---------------------------------------------------------------------------
# The assistant speaks both languages
# ---------------------------------------------------------------------------
# The Arabic answer is authored inline with each topic because Arabic is the
# house language and the phrasing has to read naturally. Doing the same twice
# would double the surface area and let the two drift apart, so English is
# derived here from the *same* payload: the headline is rebuilt from the topic,
# the bullets are rebuilt from the KPI cards, and only the narrative sentences
# that carry real meaning (sources, caveats, "what this means") get hand-written
# English below. One set of numbers, two renderings — they cannot disagree.

# Card slots per topic, by the English label the topic actually emits. Making the
# mapping explicit (rather than searching for keywords) means a new card cannot
# silently land in the wrong slot — a missing slot shows "—" instead.
_EN_SLOTS = {
    "summary":    {"revenue": 0, "expenses": 1, "net": 2, "cash": 3},
    "profit":     {"revenue": 0, "net": 3, "margin": 5},
    "expenses":   {"expenses": 1, "revenue": 2},
    "cash":       {"cash": 0},
    "receivables": {"total": 0, "over": 4},
    "payables":   {"total": 0, "over": 1},
    "inventory":  {"total": 0},
    "sales":      {"total": 0, "count": 3},
    "purchases":  {"total": 0, "count": 3},
    "payroll":    {"count": 0, "total": 1},
    "assets":     {"total": 2, "expenses": 1},
    "budgets":    {"budget": 0, "actual": 1},
    "debt":       {"total": 2, "over": 0},
    "allowance":  {"total": 0, "count": 1},
    "journals":   {"posted": 2, "draft": 1},
    "periods":    {"closed": 0, "count": 1},
    "vat":        {"net": 0},
    "statement":  {},
    "kpi":        {"margin": 0, "current": 1},
    "users":      {"count": 0},
    "audit":      {"count": 0},
    "help":       {},
}

_EN_TOPIC_HEADLINE = {
    "summary": "Net profit {net} with {cash} in liquidity",
    "profit": "Net profit {net} on revenue {revenue}",
    "expenses": "Operating expenses {expenses} on revenue {revenue}",
    "cash": "{cash} in cash and bank balances",
    "receivables": "{total} owed by customers, {over} of it over 90 days",
    "payables": "{total} owed to vendors, {over} overdue",
    "inventory": "Inventory valued at {total}",
    "sales": "Sales {total} across {count} invoices",
    "purchases": "Purchases {total} across {count} invoices",
    "payroll": "{total} basic payroll across {count} active employees",
    "assets": "Assets at {total} book value",
    "budgets": "Budget {budget} against {actual} actual",
    "debt": "Contractor debt {total} net",
    "allowance": "Subsistence {total} across {count} sheet(s)",
    "journals": "{posted} posted entries, {draft} still in draft",
    "periods": "{closed} closed periods of {count}",
    "vat": "VAT position {net}",
    "statement": "Statement figures computed from posted entries",
    "kpi": "Net margin {margin} and current ratio {current}",
    "users": "{count} users",
    "audit": "{count} recorded audit events",
    "help": "Ask me about any part of the system",
}

_EN_SOURCE = {
    "dashboard": "Dashboard and income statement (posted entries).",
    "income_statement": "Income statement, computed from posted entries.",
    "aging": "Aging report built from open posted invoices.",
    "vendor_payables": "Vendor payables (posted invoices plus the control account).",
    "inventory": "Average-cost inventory valuation.",
    "sales_invoices": "Sales invoices register.",
    "purchase_invoices": "Purchase invoices register.",
    "payroll": "Payroll register.",
    "fixed_assets": "Fixed-asset register and accumulated depreciation.",
    "budget_variance": "Budget against actual, per line.",
    "debt_position": "Contractor debt statements.",
    "allowance": "Subsistence (allowance) sheets.",
    "journal": "Journal entries.",
    "fiscal_periods": "Fiscal periods and their lock state.",
    "vat": "VAT control accounts in the general ledger.",
    "balance_sheet": "Balance sheet from the trial balance.",
    "kpi": "Ratios derived from the statement set.",
    "users": "User register.",
    "audit_log": "The audit trail.",
    "cash_position": "Cash and bank balances plus the cash-flow statement.",
    "receivables": "Customer balances from posted invoices.",
}


def _en_money(cents):
    try:
        return f"{money_str(int(cents))} EGP"
    except Exception:
        return f"{cents} EGP"


def _assistant_english(payload):
    """Derive the English rendering of an assistant payload.

    Returns ``(headline, answer)`` or ``(None, None)`` when the topic has no
    English narrative yet, in which case the caller falls back to Arabic.
    """
    topic = payload.get("topic")
    cards = payload.get("cards") or []
    data = payload.get("data") or {}
    src = _EN_SOURCE.get(payload.get("source") or "", "")

    # Bullets mirror the KPI cards exactly: label + value, same numbers.
    bullets = []
    for c in cards:
        label = c.get("label_en") or c.get("label_ar") or ""
        value = c.get("value")
        if c.get("raw") or not isinstance(value, (int, float)):
            shown = str(value)
            tone = c.get("tone")
            if tone == "danger":
                shown += " (needs attention)"
            bullets.append(f"• {label}: {shown}")
        else:
            bullets.append(f"• {label}: {_en_money(value)}")

    headline = payload.get("headline_en")
    # Some topics put a *list* in the cards (fiscal periods), so their headline
    # cannot be assembled from a couple of slots — read the real counts instead.
    if not headline:
        tpl = _EN_TOPIC_HEADLINE.get(topic)
        if tpl and cards:
            # Explicit slot map per topic: the card order differs for every topic,
            # and a keyword search quietly put revenue into the "net profit" slot.
            slots = _EN_SLOTS.get(topic) or {}

            def pick(slot_key):
                idx = slots.get(slot_key)
                if idx is None or idx >= len(cards):
                    return None
                c = cards[idx]
                v = c.get("value")
                if v is None:
                    return None
                return str(v) if c.get("raw") or not isinstance(v, (int, float)) else _en_money(v)

            subs = {k: pick(k) for k in
                    ("net", "revenue", "expenses", "pct", "cash", "net_change", "total",
                     "over", "count", "budget", "actual", "posted", "draft", "closed",
                     "margin", "current", "action")}
            subs = {k: (v if v is not None else "—") for k, v in subs.items()}
            try:
                headline = tpl.format(**subs)
            except KeyError:
                headline = None

    # Topics whose cards are not a flat value list (line items, period rows) get
    # their headline from the payload itself — after the template pass, so the
    # generic slot fill cannot overwrite it.
    if topic == "expenses":
        rev, total = payload.get("revenue_cents"), payload.get("expenses_cents")
        if rev and total:
            headline = (f"Total expenses {_en_money(total)} on revenue {_en_money(rev)} "
                        f"({_pct(total, rev)}%)")
    elif topic == "periods":
        rows = data.get("periods") or []
        closed = sum(1 for r in rows if str(r.get("status")) == "closed")
        headline = f"{closed} closed periods of {len(rows)}"

    if not topic or topic in ("help", "denied"):
        if topic == "help":
            return ("Ask me about any part of the system",
                    "I answer from your posted entries only, within your permissions.\n"
                    "Try: profit, expenses, liquidity, customer aging, vendor payables, "
                    "inventory, payroll, assets, budget variance, contractor debt, VAT, "
                    "journal entries, fiscal periods, or any financial statement.")
        return (None, None)

    if not headline:
        headline = payload.get("headline")  # Arabic fallback for the title only

    lines = [headline] if headline else []
    if bullets:
        lines.append("")
        lines.extend(bullets)
    if src:
        lines.append("")
        lines.append(f"Source: {src}")
    body = "\n".join(lines)
    return (headline, body)


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
        if getattr(self, "_diag", False):
            self.send_header("X-D1-Requests", str(_d1_client.requests_count()))
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
            user = auth_cache_get(row["user_id"])
            if user is None:
                user = load_user(conn, row["user_id"])
                auth_cache_put(row["user_id"], user)   # saves a role/permission join
            return user, hashed
        # Unknown session row. On an ephemeral (serverless) deployment the row may
        # simply live in another instance's database, so a valid signed token is
        # accepted; on a durable deployment an unknown token stays rejected.
        if EPHEMERAL_AUTH:
            claims = verify_token(raw)
            if claims:
                user = auth_cache_get(claims["user_id"]) or load_user(conn, claims["user_id"])
                if user and user["is_active"]:
                    auth_cache_put(claims["user_id"], user)
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
        # ?__diag=1 answers with X-D1-Requests: how many HTTPS round-trips this
        # request cost (the number we optimise; see docs/PERFORMANCE.md).
        self._diag = qs.pop("__diag", "") == "1"
        if self._diag:
            _d1_client.requests_reset()
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
                if fn not in (login, health, dev_accounts, demo_login):
                    if user is None:
                        raise AuthError("UNAUTHENTICATED: يلزم تسجيل الدخول / Authentication required", 401)
                    if perm:
                        require(user, perm)
                args = rx.match(path).groups()
                # ---- short-lived read cache (see "response cache" above) ----
                ckey = None
                if method == "GET" and path in CACHEABLE_GET and user is not None:
                    ckey = cache_key(path, current_company(user), qs)
                    cached = cache_get(ckey)
                    if cached is not None:
                        self._send(200, cached, "application/json; charset=utf-8",
                                   {"Cache-Control": f"private, max-age={min(CACHE_TTL, 60)}"})
                        return
                result = fn(ctx, *args)
                if ckey is not None and isinstance(result, (dict, list)):
                    cache_put(ckey, j(result))
                if isinstance(result, dict) and result.get("__file__"):
                    disp = "inline" if result.get("inline") else "attachment"
                    self._send(200, result["content"], result["content_type"],
                               {"Content-Disposition": f'{disp}; filename="{result["filename"]}"',
                                "Cache-Control": "no-store"})
                    return
                headers = ({"Cache-Control": f"private, max-age={min(CACHE_TTL, 60)}"}
                           if ckey is not None else None)
                self._send(200, j(result),
                           extra_headers=headers) if headers else self._json(200, result)
            except (ApiError,) as exc:
                self._error(exc.status, exc.message, exc.details)
            except accounting.ValidationError as exc:
                self._error(exc.status, exc.message, exc.details)
            except allowance.AllowanceError as exc:
                self._error(exc.status, exc.message)
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
            finally:
                if method != "GET":
                    cache_clear()

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
        # assets can be reused for a few minutes (the browser revalidates after
        # that); the shell (index.html) must always be fresh so a deploy is
        # picked up immediately.
        cache = "no-cache" if target.name == "index.html" else "public, max-age=300, stale-while-revalidate=86400"
        self._send(200, content, ctype, {"Cache-Control": cache})


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
        notes = [("الإجمالي: مدين %s / دائن %s — متوازن" if ar else "Totals: debit %s / credit %s — balanced")
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
        notes = [("متوازن" if data.get("balanced") else "غير متوازن")
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
    elif kind in STATEMENT_PERMISSION:
        # every other statement: one generic table built by the same flattener
        # the CSV export uses, so the PDF and the CSV can never disagree
        require(ctx.user, STATEMENT_PERMISSION.get(kind, "reports.view"))
        payload = reports.statement(ctx.conn, cid, kind, d1, d2)
        header, flat_rows = _statement_table(payload, kind)
        cols = [{"ar": h, "en": h, "w": 1.7 if i else 4.2, "align": "center" if i >= 2 else "start"}
                for i, h in enumerate(header)]
        rows = [[str(c) for c in r] for r in flat_rows]
        meta_item = next(m for m in reports.STATEMENT_META if m["id"] == kind)
        title_ar, title_en = meta_item["name_ar"], meta_item["name_en"]
        notes = []
    else:
        raise ApiError("INVALID_INPUT: نوع التقرير غير مدعوم / Unsupported report kind", 400)

    subtitle_ar = f"الفترة {exporter.fmt_date_ui(d1)} إلى {exporter.fmt_date_ui(d2)} · {company.get('name_ar', '')}"
    subtitle_en = f"Period {d1} to {d2} · {company.get('name_en', '')}"
    pdf_bytes = exporter.report_pdf(title_ar, title_en, cols, rows, company, lang,
                                    subtitle_ar, subtitle_en, notes)
    label = {"vendor-payables": "vendor-payables", "aging": "aging"}.get(kind, kind)
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
    elif kind in STATEMENT_PERMISSION:
        payload = reports.statement(ctx.conn, cid, kind, d1, d2)
        header, flat_rows = _statement_table(payload, kind)
        cols = header
        rows = [list(r) for r in flat_rows]
        money_cols = tuple(i for i, h in enumerate(header)
                           if str(h).lower() in ("amount", "opening", "additions", "reductions",
                                                  "closing", "debit", "credit", "balance", "due",
                                                  "paid", "net", "total", "remaining", "assets"))
        meta_item = next(m for m in reports.STATEMENT_META if m["id"] == kind)
        title = meta_item["name_ar"] if ar else meta_item["name_en"]
    else:
        raise ApiError("INVALID_INPUT: نوع التقرير غير مدعوم / Unsupported report kind", 400)
    content = exporter.report_xlsx(f"{title} {d1} — {d2}", cols, rows, rtl=ar, money_cols=money_cols)
    audit(ctx.conn, ctx.user, "reports.xlsx", "report", None, {"kind": kind}, company_id=cid)
    return {"__file__": True, "content": content,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"{kind}-{d1}_{d2}.xlsx"}


if __name__ == "__main__":
    main()
