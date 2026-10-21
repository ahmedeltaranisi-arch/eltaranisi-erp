"""Authentication, RBAC and audit helpers for Eltaranisi ERP."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

PBKDF2_ITERATIONS = 240_000
SESSION_HOURS = 12


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


def new_session_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Only the hash is persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


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
            "customers.create", "vendors.create",
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


def role_can(role_permissions, permission: str) -> bool:
    if "*" in role_permissions:
        return True
    if permission in role_permissions:
        return True
    module = permission.split(".")[0]
    return f"{module}.*" in role_permissions


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
