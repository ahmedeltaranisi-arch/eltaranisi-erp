"""Authentication, RBAC and audit helpers for ELTARANISI ERP."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

PBKDF2_ITERATIONS = 240_000
SESSION_HOURS = 12
_SECRET_CACHE: bytes | None = None


# --------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, per-user random salt)
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(calc.hex(), digest)
    except Exception:
        return False


def _signing_key() -> bytes:
    """Shared HMAC key for self-contained session tokens.

    Priority:
      1. ``ERP_SECRET_KEY`` (required on any multi-instance / serverless deploy —
         every instance must share it, otherwise tokens minted by one instance are
         rejected by the next one).
      2. ``data/.token-secret`` created once on first run (single-machine dev).
    """
    global _SECRET_CACHE
    if _SECRET_CACHE:
        return _SECRET_CACHE
    env = os.environ.get("ERP_SECRET_KEY")
    if env:
        _SECRET_CACHE = env.encode()
        return _SECRET_CACHE
    path = Path(os.environ.get("ERP_SECRET_FILE", "data/.token-secret"))
    try:
        if path.exists():
            _SECRET_CACHE = path.read_bytes().strip()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_hex(32).encode()
            path.write_bytes(key)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            _SECRET_CACHE = key
    except OSError:
        _SECRET_CACHE = b"eltaranisi-dev-only-key"
    return _SECRET_CACHE


def _mac(payload: str) -> str:
    return hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:43]


def new_session_token() -> tuple[str, str]:
    """Return (raw_token, token_hash).

    The raw token is **self-contained and signed** (``<payload>.<hmac>``) so any
    application instance can validate it without sharing a session table — which
    is what makes the serverless deployment (ephemeral /tmp database) usable.
    The hash is still persisted in ``sessions`` for revocation on durable
    deployments.
    """
    raw = secrets.token_urlsafe(24)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def issue_token(user_id: int) -> tuple[str, str]:
    """Signed bearer token: base64(payload).hmac — verifiable without the DB."""
    exp = int((datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).timestamp())
    payload = f"u{user_id}.{exp}.{secrets.token_hex(6)}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    token = f"{encoded}.{_mac(payload)}"
    return token, hashlib.sha256(token.encode()).hexdigest()


def verify_token(raw: str) -> dict | None:
    """Validate the signature and expiry of a token; return its claims or None."""
    try:
        encoded, sig = raw.rsplit(".", 1)
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding).decode()
        if not hmac.compare_digest(sig, _mac(payload)):
            return None
        uid, exp, _nonce = payload.split(".")
        if int(exp) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return {"user_id": int(uid.lstrip("u")), "exp": int(exp)}
    except Exception:
        return None


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def session_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# Permission catalogue
# --------------------------------------------------------------------------
PERMISSIONS = {
    "dashboard.view": "عرض لوحة التحكم / View dashboard",
    "accounts.view": "عرض دليل الحسابات / View chart of accounts",
    "accounts.create": "إنشاء وتعديل الحسابات / Create & edit accounts",
    "journal.view": "عرض القيود / View journal entries",
    "journal.create": "إنشاء قيود / Create journal entries",
    "journal.review": "مراجعة القيود / Review entries",
    "journal.approve": "اعتماد القيود / Approve entries",
    "journal.post": "ترحيل القيود / Post entries",
    "journal.reverse": "عكس القيود / Reverse entries",
    "period.close": "إقفال الفترات المالية / Close fiscal periods",
    "customers.view": "عرض العملاء / View customers",
    "customers.create": "إدارة العملاء / Manage customers",
    "vendors.view": "عرض الموردين / View vendors",
    "vendors.create": "إدارة الموردين / Manage vendors",
    "sales.view": "عرض المبيعات / View sales",
    "sales.create": "إنشاء فواتير البيع / Create sales invoices",
    "sales.post": "ترحيل المبيعات / Post sales invoices",
    "purchases.view": "عرض المشتريات / View purchases",
    "purchases.create": "إنشاء فواتير الشراء / Create purchase invoices",
    "purchases.post": "ترحيل المشتريات / Post purchase invoices",
    "payments.view": "عرض المدفوعات والتحصيلات / View payments",
    "payments.create": "إنشاء مدفوعات وتحصيلات / Create payments",
    "payments.post": "ترحيل المدفوعات / Post payments",
    "payables.view": "عرض مديونية الموردين / View vendor payables",
    "banks.view": "عرض البنوك والصناديق / View banks",
    "banks.manage": "إدارة البنوك والتسويات / Manage banks & reconciliation",
    "inventory.view": "عرض المخزون / View inventory",
    "inventory.manage": "إدارة المخزون / Manage inventory",
    "assets.view": "عرض الأصول / View fixed assets",
    "assets.manage": "إدارة الأصول والإهلاك / Manage assets & depreciation",
    "payroll.view": "عرض الرواتب / View payroll",
    "payroll.manage": "إدارة الرواتب / Manage payroll",
    "budgets.view": "عرض الموازنات / View budgets",
    "budgets.manage": "إدارة الموازنات / Manage budgets",
    "reports.view": "عرض التقارير المالية / View financial reports",
    "reports.export": "تصدير التقارير / Export reports",
    "users.manage": "إدارة المستخدمين والصلاحيات / Manage users & roles",
    "settings.manage": "إعدادات الشركة / Company settings",
    "audit.view": "سجل التدقيق / Audit log",
    "ai.use": "المساعد المالي الذكي / AI finance assistant",
    "platform.manage": "إدارة المنصة (بدون بيانات مالية) / Platform admin",
    "journal.delete": "حذف القيود اليدوية / Delete manual journal entries",
    "sales.delete": "حذف فواتير المبيعات / Delete sales invoices",
    "purchases.delete": "حذف فواتير المشتريات / Delete purchase invoices",
    "payments.delete": "حذف سندات التحصيل والدفع / Delete receipts & payments",
    "customers.delete": "حذف العملاء / Delete customers",
    "vendors.delete": "حذف الموردين / Delete vendors",
    "customers.edit": "تعديل بيانات العملاء / Edit customers",
    "vendors.edit": "تعديل بيانات الموردين / Edit vendors",
    "inventory.edit": "تعديل الأصناف / Edit products",
    "assets.edit": "تعديل الأصول / Edit fixed assets",
    "payroll.edit": "تعديل الموظفين / Edit employees",
    "banks.edit": "تعديل البنوك والصناديق / Edit banks & cash boxes",
    "journal.edit": "تعديل القيود اليدوية / Edit manual journal entries",
    "sales.edit": "تعديل فواتير البيع / Edit sales invoices",
    "purchases.edit": "تعديل فواتير الشراء / Edit purchase invoices",
    "payments.edit": "تعديل سندات التحصيل والدفع / Edit receipts & payments",
    "budgets.edit": "تعديل بنود الموازنة / Edit budget lines",
}

# Editing is a strictly narrower right than creating/managing the same module:
# whoever may create a record may correct it while it is still a draft, and the
# dedicated ``*.edit`` permissions below are granted automatically to the roles
# that hold the corresponding create/manage permission (they can also be granted
# on their own from the roles screen without granting creation).
EDIT_IMPLIED_BY = {
    "customers.edit": "customers.create",
    "vendors.edit": "vendors.create",
    "inventory.edit": "inventory.manage",
    "assets.edit": "assets.manage",
    "payroll.edit": "payroll.manage",
    "banks.edit": "banks.manage",
    "journal.edit": "journal.create",
    "sales.edit": "sales.create",
    "purchases.edit": "purchases.create",
    "payments.edit": "payments.create",
    "budgets.edit": "budgets.manage",
}

FINANCIAL_READ = [
    "dashboard.view", "accounts.view", "journal.view", "customers.view", "vendors.view",
    "sales.view", "purchases.view", "payments.view", "payables.view", "banks.view",
    "inventory.view", "assets.view", "reports.view", "budgets.view",
]

ROLE_DEFINITIONS = [
    {
        "code": "super_admin",
        "name_ar": "مدير المنصة", "name_en": "Super Admin",
        "data_scope": "platform",
        "permissions": ["platform.manage", "users.manage", "audit.view"],
    },
    {
        "code": "company_owner",
        "name_ar": "مالك الشركة", "name_en": "Company Owner",
        "data_scope": "company",
        "permissions": ["*"],
    },
    {
        "code": "financial_manager",
        "name_ar": "المدير المالي", "name_en": "Financial Manager",
        "data_scope": "company",
        "permissions": FINANCIAL_READ + [
            "accounts.create", "journal.create", "journal.review", "journal.approve",
            "journal.post", "journal.reverse", "period.close", "payments.create",
            "payments.post", "sales.post", "purchases.post", "budgets.manage",
            "reports.export", "payroll.view", "banks.manage", "ai.use", "audit.view",
            "customers.create", "vendors.create", "customers.delete", "vendors.delete",
            "sales.delete", "purchases.delete", "payments.delete", "journal.delete",
        ],
    },
    {
        "code": "chief_accountant",
        "name_ar": "رئيس الحسابات", "name_en": "Chief Accountant",
        "data_scope": "company",
        "permissions": FINANCIAL_READ + [
            "accounts.create", "journal.create", "journal.review", "journal.post",
            "journal.reverse", "sales.create", "sales.post", "purchases.create",
            "purchases.post", "payments.create", "payments.post", "inventory.manage",
            "assets.manage", "reports.export", "ai.use",
            "sales.delete", "purchases.delete", "payments.delete", "journal.delete",
        ],
    },
    {
        "code": "accountant",
        "name_ar": "محاسب", "name_en": "Accountant",
        "data_scope": "company",
        "permissions": FINANCIAL_READ + [
            "journal.create", "sales.create", "purchases.create", "payments.create",
            "inventory.view", "reports.export", "ai.use",
        ],
    },
    {
        "code": "sales_user",
        "name_ar": "مسؤول مبيعات", "name_en": "Sales User",
        "data_scope": "company",
        "permissions": ["dashboard.view", "sales.view", "sales.create", "customers.view",
                        "customers.create", "inventory.view", "accounts.view"],
    },
    {
        "code": "purchasing_user",
        "name_ar": "مسؤول مشتريات", "name_en": "Purchasing User",
        "data_scope": "company",
        "permissions": ["dashboard.view", "purchases.view", "purchases.create", "vendors.view",
                        "vendors.create", "inventory.view", "payables.view", "accounts.view"],
    },
    {
        "code": "inventory_user",
        "name_ar": "مسؤول مخزون", "name_en": "Inventory User",
        "data_scope": "company",
        "permissions": ["dashboard.view", "inventory.view", "inventory.manage", "accounts.view"],
    },
    {
        "code": "hr_user",
        "name_ar": "مسؤول موارد بشرية", "name_en": "HR User",
        "data_scope": "company",
        "permissions": ["dashboard.view", "payroll.view", "payroll.manage"],
    },
    {
        "code": "auditor",
        "name_ar": "مراجع (اطلاع فقط)", "name_en": "Auditor (read-only)",
        "data_scope": "read_only",
        "permissions": FINANCIAL_READ + ["payroll.view", "reports.export", "audit.view", "ai.use"],
    },
]


def sync_system_roles(conn) -> int:
    """Refresh the permission set of every built-in role from this module.

    Roles are code, not data: a deployment that adds a permission (for example
    the ``*.edit`` family) must reach databases that were seeded before it, and
    an administrator cannot accidentally strip rights from a system role in a
    way the code does not expect. Custom roles are never touched.
    """
    updated = 0
    for role in ROLE_DEFINITIONS:
        row = conn.execute("SELECT permissions, is_system FROM roles WHERE code=?",
                           (role["code"],)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO roles (code, name_ar, name_en, permissions, data_scope, is_system)
                   VALUES (?,?,?,?,?,1)""",
                (role["code"], role["name_ar"], role["name_en"],
                 json.dumps(role["permissions"]), role["data_scope"]))
            updated += 1
            continue
        if row["is_system"] and row["permissions"] != json.dumps(role["permissions"]):
            conn.execute("UPDATE roles SET permissions=?, name_ar=?, name_en=?, data_scope=? WHERE code=?",
                         (json.dumps(role["permissions"]), role["name_ar"], role["name_en"],
                          role["data_scope"], role["code"]))
            updated += 1
    return updated


def role_can(role_permissions, permission: str) -> bool:
    if "*" in role_permissions:
        return True
    if permission in role_permissions:
        return True
    module = permission.split(".")[0]
    if f"{module}.*" in role_permissions:
        return True
    parent = EDIT_IMPLIED_BY.get(permission)
    if parent and parent in role_permissions:
        return True
    return False


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


class PermissionError_(Exception):
    def __init__(self, message: str = "FORBIDDEN"):
        super().__init__(message)
        self.message = message
        self.status = 403


def load_user(conn, user_id: int):
    row = conn.execute(
        """SELECT u.*, r.permissions AS role_permissions, r.name_ar AS role_name_ar,
                  r.name_en AS role_name_en, r.data_scope
           FROM users u JOIN roles r ON r.code = u.role_code WHERE u.id = ?""",
        (user_id,),
    ).fetchone()
    return row


def public_user(row) -> dict:
    perms = json.loads(row["role_permissions"] or "[]")
    return {
        "id": row["id"],
        "username": row["username"],
        "name_ar": row["full_name_ar"],
        "name_en": row["full_name_en"],
        "email": row["email"],
        "role": row["role_code"],
        "role_name_ar": row["role_name_ar"],
        "role_name_en": row["role_name_en"],
        "company_id": row["company_id"],
        "branch_id": row["branch_id"],
        "language": row["language"],
        "theme": row["theme"],
        "permissions": perms,
        "password_must_change": row["password_must_change"],
    }


def require(user_row, permission: str):
    perms = json.loads(user_row["role_permissions"] or "[]")
    if not role_can(perms, permission):
        raise PermissionError_("FORBIDDEN")


def has(user_row, permission: str) -> bool:
    perms = json.loads(user_row["role_permissions"] or "[]")
    return role_can(perms, permission)


def audit(conn, user, action: str, entity: str = None, entity_id=None, details=None,
          company_id=None, ip=None):
    conn.execute(
        """INSERT INTO audit_logs (company_id, user_id, username, action, entity, entity_id, details, ip, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            company_id if company_id is not None else (user["company_id"] if user else None),
            user["id"] if user else None,
            user["username"] if user else "system",
            action, entity, entity_id,
            json.dumps(details, ensure_ascii=False, default=str) if details is not None else None,
            ip, utcnow(),
        ),
    )


def notify(conn, company_id, user_id, type_, title_ar, title_en, body_ar="", body_en="", link=None):
    conn.execute(
        """INSERT INTO notifications (company_id, user_id, type, title_ar, title_en, body_ar, body_en, link, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (company_id, user_id, type_, title_ar, title_en, body_ar, body_en, link, utcnow()),
    )
