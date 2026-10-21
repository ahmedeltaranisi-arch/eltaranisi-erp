"""Development seed data for ELTARANISI ERP.

Creates one organization, one company with branches, the full chart of accounts,
real users (Ahmed Eltaranisi = Company Owner, Mahmoud Morad = Financial Manager),
master data, and nine months of transactions posted through the real accounting
engine so every report is internally consistent.

Passwords are generated randomly at seed time and written to
data/dev-credentials.txt (development only — never commit / never use in production).
"""
from __future__ import annotations

import random
import os
from pathlib import Path
import secrets
from datetime import date, timedelta

import accounting
import documents
import db
from db import init_db, transaction, to_milli
from security import ROLE_DEFINITIONS, hash_password, utcnow

random.seed(20261020)

TODAY = date.today()
YEAR = TODAY.year

ACCOUNTS = [
    # (code, name_ar, name_en, type, parent_code, postable)
    ("1", "الأصول", "Assets", "asset", None, 0),
    ("11", "الأصول المتداولة", "Current Assets", "asset", "1", 0),
    ("111", "النقدية وما في حكمها", "Cash & Cash Equivalents", "asset", "11", 0),
    ("1111", "صندوق دمياط الرئيسي", "Damietta Main Cash Box", "asset", "111", 1),
    ("1112", "صندوق فرع القاهرة", "Cairo Branch Cash Box", "asset", "111", 1),
    ("112", "النقدية بالبنوك", "Cash at Banks", "asset", "11", 0),
    ("1121", "بنك مصر — حساب جاري", "Banque Misr — Current Account", "asset", "112", 1),
    ("1122", "البنك الأهلي المصري — حساب جاري", "NBE — Current Account", "asset", "112", 1),
    ("12", "المدينون", "Receivables", "asset", "1", 0),
    ("1210", "العملاء", "Trade Receivables — Customers", "asset", "12", 1),
    ("1220", "سلف وأمانات الموظفين", "Employee Advances", "asset", "12", 1),
    ("1260", "ضريبة القيمة المضافة — مشتريات", "VAT Input", "asset", "12", 1),
    ("13", "المخزون والأصول الأخرى", "Inventory & Other Assets", "asset", "1", 0),
    ("1310", "المخزون", "Inventory", "asset", "13", 1),
    ("1330", "مجمع إهلاك الأصول الثابتة", "Accumulated Depreciation", "asset", "13", 1),
    ("14", "الأصول الثابتة", "Fixed Assets", "asset", "1", 0),
    ("1410", "أراضي", "Land", "asset", "14", 1),
    ("1420", "مباني وإنشاءات", "Buildings", "asset", "14", 1),
    ("1430", "سيارات ووسائل نقل", "Vehicles", "asset", "14", 1),
    ("1440", "أجهزة حاسوب وبرامج", "Computers & Software", "asset", "14", 1),
    ("1450", "أثاث ومفروشات", "Furniture & Fixtures", "asset", "14", 1),
    ("2", "الالتزامات", "Liabilities", "liability", None, 0),
    ("21", "الالتزامات المتداولة", "Current Liabilities", "liability", "2", 0),
    ("2110", "الموردون", "Trade Payables — Vendors", "liability", "21", 1),
    ("2120", "مستحقات المقاولين والموردين المتنوعين", "Contractor & Sundry Payables", "liability", "21", 1),
    ("2130", "رواتب مستحقة", "Accrued Payroll", "liability", "21", 1),
    ("2135", "تأمينات وضرائب مستحقة", "Payroll Deductions Payable", "liability", "21", 1),
    ("2140", "مصروفات مستحقة", "Accrued Expenses", "liability", "21", 1),
    ("22", "الضرائب", "Taxes", "liability", "2", 0),
    ("2210", "ضريبة القيمة المضافة — مبيعات", "VAT Output", "liability", "22", 1),
    ("2220", "ضريبة كسب العمل", "Payroll Tax Payable", "liability", "22", 1),
    ("23", "قروض وتسهيلات", "Loans & Facilities", "liability", "2", 0),
    ("2310", "قروض بنكية طويلة الأجل", "Long-term Bank Loans", "liability", "23", 1),
    ("3", "حقوق الملكية", "Equity", "equity", None, 0),
    ("3100", "رأس المال المدفوع", "Paid-in Capital", "equity", "3", 1),
    ("3200", "أرباح محتجزة", "Retained Earnings", "equity", "3", 1),
    ("3900", "أرباح الفترة (غير مقفلة)", "Current Period Result", "equity", "3", 1),
    ("4", "الإيرادات", "Revenue", "revenue", None, 0),
    ("41", "إيرادات النشاط", "Operating Revenue", "revenue", "4", 0),
    ("4100", "إيرادات المبيعات", "Sales Revenue", "revenue", "41", 1),
    ("4110", "إيرادات خدمات", "Service Revenue", "revenue", "41", 1),
    ("4200", "مردودات وخصومات المبيعات", "Sales Returns & Discounts", "revenue", "41", 1),
    ("4900", "إيرادات أخرى", "Other Revenue", "revenue", "4", 1),
    ("5", "المصروفات", "Expenses", "expense", None, 0),
    ("51", "تكلفة النشاط", "Cost of Sales", "expense", "5", 0),
    ("5100", "تكلفة المبيعات", "Cost of Goods Sold", "expense", "51", 1),
    ("52", "مصروفات تشغيلية", "Operating Expenses", "expense", "5", 0),
    ("5200", "رواتب وأجور", "Salaries & Wages", "expense", "52", 1),
    ("5210", "بدلات ومزايا", "Allowances & Benefits", "expense", "52", 1),
    ("5300", "إيجارات", "Rent", "expense", "52", 1),
    ("5310", "كهرباء ومياه", "Utilities", "expense", "52", 1),
    ("5320", "مصروف الإهلاك", "Depreciation Expense", "expense", "52", 1),
    ("5330", "مصروفات بيعية وتسويقية", "Selling & Marketing", "expense", "52", 1),
    ("5340", "مصروفات إدارية وعمومية", "General & Administrative", "expense", "52", 1),
    ("5350", "اتصالات وإنترنت", "Communications & Internet", "expense", "52", 1),
    ("5360", "نقل وشحن", "Freight & Delivery", "expense", "52", 1),
    ("5400", "مصروفات بنكية", "Bank Charges", "expense", "52", 1),
    ("5900", "مصروفات أخرى", "Other Expenses", "expense", "5", 1),
]

COST_CENTERS = [
    ("CC-ADM", "الإدارة العامة", "Administration"),
    ("CC-SAL", "المبيعات", "Sales"),
    ("CC-WHS", "المخازن", "Warehousing"),
    ("CC-DLV", "التوصيل", "Delivery"),
]

CUSTOMERS = [
    ("C-1001", "شركة النيل للاستيراد والتصدير", "Nile Import & Export Co.", "01001234567", 5_000_000_00, 30),
    ("C-1002", "مؤسسة الدلتا للتجارة الدولية", "Delta International Trading", "01112345678", 3_000_000_00, 45),
    ("C-1003", "الشركة المصرية للحاصلات الزراعية", "Egyptian Agri-Export Co.", "01223456789", 1_000_000_00, 30),
    ("C-1004", "شركة البحر المتوسط للشحن", "Mediterranean Shipping Co.", "01534567890", 8_000_000_00, 60),
    ("C-1005", "عميل نقدي", "Cash Customer", None, 0, 0),
    ("C-1006", "مخازن الأمانة اللوجستية", "Al-Amana Logistics", "01045678901", 2_000_000_00, 30),
]

VENDORS = [
    ("V-2001", "شركة الشرق للتوكيلات الملاحية", "Al-Sharq Maritime Agencies", "01009876543", 30),
    ("V-2002", "ميناء دمياط — هيئة الميناء", "Damietta Port Authority", "01119876543", 45),
    ("V-2003", "شركة النور لأعمال التفريغ", "Al-Nour Stevedoring Co.", "01229876543", 30),
    ("V-2004", "مؤسسة الأمل للخدمات اللوجستية", "Al-Amal Logistics Est.", "01539876543", 60),
    ("V-2005", "شركة الدقهلية لقطع غيار المعدات", "Dakahlia Equipment Spare Parts", "01049876543", 30),
    ("V-2006", "موردون متنوعون", "Sundry Suppliers", None, 15),
]

# Demo catalogue — used only with ERP_SEED_MODE=demo (integration tests need
# stocked products to exercise FIFO/weighted-average and COGS postings).
PRODUCTS_DEMO = [
    ("SKU-001", "أرز مصري فاخر ٥ كجم", "Premium Egyptian Rice 5kg", "كيس", 18_500, 14_200, 1400, 60),
    ("SKU-002", "زيت ذرة ١ لتر", "Corn Oil 1L", "زجاجة", 9_750, 7_100, 1400, 120),
    ("SKU-003", "سكر أبيض ١ كجم", "White Sugar 1kg", "كيس", 4_200, 3_100, 1400, 200),
    ("SKU-004", "مكرونة إسباجتي ٤٠٠ جم", "Spaghetti 400g", "عبوة", 2_800, 1_950, 1400, 300),
    ("SKU-005", "شاي أسود ٢٥٠ جم", "Black Tea 250g", "علبة", 7_500, 5_400, 1400, 150),
    ("SKU-006", "لبن مجفف ٤٠٠ جم", "Milk Powder 400g", "علبة", 14_000, 10_800, 1400, 80),
    ("SKU-007", "خدمة توصيل ونقل", "Delivery Service", "خدمة", 50_000, 0, 1400, 0),
    ("SKU-008", "منظفات متعددة الاستخدام", "Multi-purpose Detergent", "زجاجة", 5_500, 3_800, 1400, 90),
]
PRODUCTS = [
    ("SRV-001", "خدمة شحن بحري (حاوية ٢٠ قدم)", "Ocean Freight (20ft Container)", "حاوية", 1_250_000, 980_000, 1400, 0),
    ("SRV-002", "خدمة شحن بحري (حاوية ٤٠ قدم)", "Ocean Freight (40ft Container)", "حاوية", 2_150_000, 1_720_000, 1400, 0),
    ("SRV-003", "أعمال تفريغ وشحن بالميناء", "Port Loading & Discharging", "عملية", 480_000, 360_000, 1400, 0),
    ("SRV-004", "تخليص جمركي — إفراج نهائي", "Customs Clearance — Final Release", "ملف", 320_000, 210_000, 1400, 0),
    ("SRV-005", "وكالة ملاحية للسفن", "Ship Agency Services", "سفينة", 1_800_000, 1_300_000, 1400, 0),
    ("SRV-006", "تخزين بمنطقة الميناء", "Port Warehousing", "يوم/متر", 65_000, 42_000, 1400, 0),
    ("SRV-007", "نقل بري من/إلى الميناء", "Inland Trucking To/From Port", "نقلة", 410_000, 300_000, 1400, 0),
    ("SRV-008", "أعمال توكيل وتخليص متنوعة", "Sundry Agency & Clearance", "خدمة", 150_000, 95_000, 1400, 0),
]

WAREHOUSES = [
    ("WH-01", "المخزن الرئيسي — دمياط", "Main Warehouse — Damietta"),
    ("WH-02", "مخزن فرع القاهرة", "Cairo Branch Warehouse"),
]

EMPLOYEES = [
    ("EMP-001", "أحمد الطرانيسي", "Ahmed Eltaranisi", "الإدارة", "مدير الشركة", 25_000_00, 5_000_00, 0, 0, 0),
    ("EMP-002", "محمود مراد", "Mahmoud Morad", "الإدارة المالية", "المدير المالي", 18_000_00, 3_000_00, 0, 1_200_00, 2_400_00),
    ("EMP-003", "سامي عبد الرحمن", "Samy Abdelrahman", "المحاسبة", "رئيس الحسابات", 14_000_00, 2_000_00, 0, 800_00, 1_300_00),
    ("EMP-004", "منى سليم", "Mona Selim", "المحاسبة", "محاسب", 9_000_00, 1_000_00, 0, 480_00, 520_00),
    ("EMP-005", "كريم فتحي", "Karim Fathy", "المخازن", "أمين مخزن", 7_000_00, 800_00, 0, 360_00, 240_00),
    ("EMP-006", "طارق عادل", "Tarek Adel", "المبيعات", "مسؤول مبيعات", 8_000_00, 2_000_00, 0, 440_00, 410_00),
]

ASSETS = [
    ("FA-001", "سيارة نقل هينو", "Hino Delivery Truck", "سيارات", 850_000_00, 85_000_00, 96, "1430"),
    ("FA-002", "أثاث مكتبي — المقر", "Office Furniture — HQ", "أثاث", 240_000_00, 24_000_00, 120, "1450"),
    ("FA-003", "أجهزة حاسوب وخدمات شبكة", "Computers & Network", "حاسبات", 180_000_00, 18_000_00, 48, "1440"),
    ("FA-004", "مبنى المخزن الرئيسي", "Main Warehouse Building", "مباني", 2_400_000_00, 240_000_00, 240, "1420"),
]

# Fixed development passwords (demo environment only).
# In production set ERP_DEV_FIXED_PASSWORDS=false and inject real credentials
# through the user-management screen / secrets manager — never ship this file.
DEV_PASSWORDS = {
    "ahmed.taranisi": "Eltaranisi@2026",
    "mahmoud.morad": "Mahmoud@2026",
    "samy.abdelrahman": "Samy@2026",
    "mona.selim": "Mona@2026",
    "karim.fathy": "Karim@2026",
    "tarek.adel": "Tarek@2026",
    "heba.elsherif": "Heba@2026",
}
FIXED_PASSWORDS = os.environ.get("ERP_DEV_FIXED_PASSWORDS", "true").lower() not in ("0", "false", "no")

# clean (default) → company + chart of accounts + users + master data only, every
# balance is ZERO so the business can start from scratch.
# demo            → nine months of transactions, opening balances and stock
#                   (needed by tests/test_erp.py, never used in production).
SEED_MODE = os.environ.get("ERP_SEED_MODE", "clean").strip().lower()
# Backwards compatibility: the WSGI bridge / CI entrypoints still set
# ERP_SEED_DEMO_DATA=true to ask for the full transaction history.
_LEGACY_DEMO = os.environ.get("ERP_SEED_DEMO_DATA", "").strip().lower() in ("1", "true", "yes", "on")
DEMO = SEED_MODE == "demo" or _LEGACY_DEMO

USERS = [
    ("ahmed.taranisi", "أحمد الطرانيسي", "Ahmed Eltaranisi", "company_owner", "ahmed@eltaranisi.com"),
    ("mahmoud.morad", "محمود مراد", "Mahmoud Morad", "financial_manager", "mahmoud@eltaranisi.com"),
    ("samy.abdelrahman", "سامي عبد الرحمن", "Samy Abdelrahman", "chief_accountant", "samy@eltaranisi.com"),
    ("mona.selim", "منى سليم", "Mona Selim", "accountant", "mona@eltaranisi.com"),
    ("karim.fathy", "كريم فتحي", "Karim Fathy", "inventory_user", "karim@eltaranisi.com"),
    ("tarek.adel", "طارق عادل", "Tarek Adel", "sales_user", "tarek@eltaranisi.com"),
    ("heba.elsherif", "هبة الشريف", "Heba Elsherif", "auditor", "heba@eltaranisi.com"),
]


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def seed():
    # A demo reset is destructive. On a shared/cloud database (Cloudflare D1)
    # it may only happen when the operator says so explicitly.
    if db.is_d1() and (os.environ.get("ERP_SEED_FORCE") or "").lower() not in ("1", "true", "yes"):
        conn = db.connect()
        try:
            users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        except Exception:
            users = 0
        finally:
            conn.close()
        if users:
            raise SystemExit(
                "✗ this Cloudflare D1 database already holds data (%d users).\n"
                "  Seeding would wipe it. Re-run with ERP_SEED_FORCE=1 if that is really what you want." % users)
    init_db(force=True)
    creds = []
    with transaction() as conn:
        # ---------------- org / company / branches ----------------
        conn.execute("INSERT INTO organizations (code, name_ar, name_en) VALUES ('SSM','مجموعة سي سيرفيس','Sea Service Group')")
        org_id = conn.execute("SELECT id FROM organizations").fetchone()["id"]
        conn.execute(
            """INSERT INTO companies (org_id, code, name_ar, name_en, base_currency, tax_number, address, phone)
               VALUES (?,?,?,?,?,?,?,?)""",
            (org_id, "SSM-01", "شركة سي سيرفيس للشحن والتفريغ والوكالة الملاحية",
             "Sea Service Co. for Shipping, Discharging & Maritime Agency",
             "EGP", "512-345-678", "دمياط — ميناء دمياط الجديد — برج سي سيرفيس", "+20 57 234 5678"))
        company_id = conn.execute("SELECT id FROM companies").fetchone()["id"]
        conn.execute("INSERT INTO branches (company_id, code, name_ar, name_en) VALUES (?,?,?,?)",
                     (company_id, "BR-01", "الإدارة العامة — ميناء دمياط", "Head Office — Damietta Port"))
        conn.execute("INSERT INTO branches (company_id, code, name_ar, name_en) VALUES (?,?,?,?)",
                     (company_id, "BR-02", "مكتب القاهرة", "Cairo Office"))
        branches = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM branches").fetchall()}
        br_main = branches["BR-01"]

        # ---------------- roles ----------------
        for r in ROLE_DEFINITIONS:
            conn.execute(
                "INSERT INTO roles (code, name_ar, name_en, permissions, data_scope, is_system) VALUES (?,?,?,?,?,1)",
                (r["code"], r["name_ar"], r["name_en"], __import__("json").dumps(r["permissions"]),
                 r["data_scope"]))

        # ---------------- users ----------------
        users = {}
        for username, name_ar, name_en, role, email in USERS:
            pwd = DEV_PASSWORDS[username] if FIXED_PASSWORDS else secrets.token_urlsafe(9)
            cur = conn.execute(
                """INSERT INTO users (company_id, branch_id, username, full_name_ar, full_name_en, email,
                       password_hash, role_code, language, theme, password_must_change)
                   VALUES (?,?,?,?,?,?,?,?,'ar','light',0)""",
                (company_id, br_main, username, name_ar, name_en, email, hash_password(pwd), role))
            users[username] = cur.lastrowid
            conn.execute("INSERT INTO user_company_access (user_id, company_id) VALUES (?,?)",
                         (cur.lastrowid, company_id))
            creds.append((username, name_ar, role, pwd))

        ahmed = conn.execute("SELECT * FROM users WHERE username='ahmed.taranisi'").fetchone()
        mahmoud = conn.execute("SELECT * FROM users WHERE username='mahmoud.morad'").fetchone()
        samy = conn.execute("SELECT * FROM users WHERE username='samy.abdelrahman'").fetchone()
        mona = conn.execute("SELECT * FROM users WHERE username='mona.selim'").fetchone()
        karim = conn.execute("SELECT * FROM users WHERE username='karim.fathy'").fetchone()
        tarek = conn.execute("SELECT * FROM users WHERE username='tarek.adel'").fetchone()

        # ---------------- fiscal years / periods ----------------
        for year in (YEAR - 1, YEAR, YEAR + 1):
            conn.execute("INSERT INTO fiscal_years (company_id, name, start_date, end_date) VALUES (?,?,?,?)",
                         (company_id, f"السنة المالية {year}", f"{year}-01-01", f"{year}-12-31"))
            fy = conn.execute("SELECT id FROM fiscal_years WHERE company_id=? AND name=?",
                              (company_id, f"السنة المالية {year}")).fetchone()["id"]
            for m in range(1, 13):
                start = date(year, m, 1)
                end = (add_months(start, 1) - timedelta(days=1))
                conn.execute(
                    """INSERT INTO fiscal_periods (company_id, fiscal_year_id, period_no, start_date, end_date, status)
                       VALUES (?,?,?,?,?,?)""",
                    (company_id, fy, m, start.isoformat(), end.isoformat(),
                     "closed" if year < YEAR else "open"))
        fy_current = conn.execute("SELECT id FROM fiscal_years WHERE company_id=? AND name=?",
                                  (company_id, f"السنة المالية {YEAR}")).fetchone()["id"]

        # ---------------- chart of accounts ----------------
        code_to_id = {}
        for code, name_ar, name_en, type_, parent, postable in ACCOUNTS:
            parent_id = code_to_id.get(parent) if parent else None
            level = len(code)
            cur = conn.execute(
                """INSERT INTO accounts (company_id, code, name_ar, name_en, type, parent_id, level,
                       is_postable, currency, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (company_id, code, name_ar, name_en, type_, parent_id, level, postable, "EGP", ahmed["id"]))
            code_to_id[code] = cur.lastrowid

        # ---------------- cost centers / projects ----------------
        cc = {}
        for code, ar, en in COST_CENTERS:
            cur = conn.execute("INSERT INTO cost_centers (company_id, code, name_ar, name_en) VALUES (?,?,?,?)",
                               (company_id, code, ar, en))
            cc[code] = cur.lastrowid
        conn.execute("""INSERT INTO projects (company_id, code, name_ar, name_en, cost_center_id)
                        VALUES (?,?,?,?,?)""",
                     (company_id, "PRJ-01", "مشروع تشغيل رصيف الحاويات", "Container Berth Operation", cc["CC-SAL"]))
        conn.execute("""INSERT INTO projects (company_id, code, name_ar, name_en, cost_center_id)
                        VALUES (?,?,?,?,?)""",
                     (company_id, "PRJ-02", "مشروع تحديث معدات الميناء", "Port Equipment Upgrade", cc["CC-WHS"]))
        projects = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM projects").fetchall()}

        # ---------------- warehouses / products ----------------
        wh = {}
        for code, ar, en in WAREHOUSES:
            cur = conn.execute("INSERT INTO warehouses (company_id, branch_id, code, name_ar, name_en) VALUES (?,?,?,?,?)",
                               (company_id, br_main if code == "WH-01" else branches["BR-02"], code, ar, en))
            wh[code] = cur.lastrowid
        products = {}
        for sku, ar, en, unit, sale, cost, tax_bp, reorder in (PRODUCTS_DEMO if DEMO else PRODUCTS):
            cur = conn.execute(
                """INSERT INTO products (company_id, sku, name_ar, name_en, unit, category, is_service,
                       sale_price_cents, purchase_price_cents, tax_rate_bp, reorder_level_milli,
                       inventory_account_id, cogs_account_id, revenue_account_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (company_id, sku, ar, en, unit,
                 "خدمات ملاحية" if not DEMO else ("خدمات" if sku == "SKU-007" else "مواد غذائية"),
                 1 if (sku == "SKU-007" or not DEMO) else 0, sale, cost, tax_bp,
                 0 if not DEMO else reorder * 1000,
                 None if (not DEMO or sku == "SKU-007") else code_to_id["1310"],
                 None if (not DEMO or sku == "SKU-007") else code_to_id["5100"],
                 code_to_id["4110"] if (not DEMO or sku == "SKU-007") else code_to_id["4100"]))
            products[sku] = cur.lastrowid

        # ---------------- customers / vendors ----------------
        customers = {}
        for code, ar, en, phone, limit, terms in CUSTOMERS:
            cur = conn.execute(
                """INSERT INTO customers (company_id, code, name_ar, name_en, phone, credit_limit_cents,
                       payment_terms_days, account_id) VALUES (?,?,?,?,?,?,?,?)""",
                (company_id, code, ar, en, phone, limit, terms, code_to_id["1210"]))
            customers[code] = cur.lastrowid
        vendors = {}
        for code, ar, en, phone, terms in VENDORS:
            cur = conn.execute(
                """INSERT INTO vendors (company_id, code, name_ar, name_en, phone, payment_terms_days, account_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (company_id, code, ar, en, phone, terms, code_to_id["2110"]))
            vendors[code] = cur.lastrowid

        # ---------------- مديونية: contractor & supplier accounts ----------------
        # The chart of accounts mirrors the owner's paper debt statement
        # (بيان مديونية مقاولين متنوعة ميناء دمياط). Balances start at zero — the
        # company asked for a clean slate — but the structure, units and types are
        # the real ones: tonnage, trips, containers, monthly hire, and standing
        # supplier accounts.
        DEBT_ACCOUNTS = [
            ("DB-0001", "ح / مقاولات الهندسية للتصميمات", "Engineering Designs Co.", "contractor", "item", None),
            ("DB-0002", "ح / الهندسية للهيدروليك", "Engineering Hydraulics", "contractor", "item", None),
            ("DB-0003", "ح / إبراهيم الحبشي — نقل بالطن", "Ibrahim El-Habashi (per ton)", "contractor", "ton", None),
            ("DB-0004", "ح / رامي الحديدي", "Ramy El-Hadidy", "contractor", "item", None),
            ("DB-0005", "ح / سي ترانس — نقل سيارات", "C Trans — vehicle transport", "transport", "trip", None),
            ("DB-0006", "ح / مكتب القدس لأعمال النقل والحاويات", "Al-Quds Container Transport Office",
             "transport", "container", None),
            ("DB-0007", "ح / اللضام — أعمال شحن وتفريغ", "Al-Ladam stevedoring", "contractor", "ton", None),
            ("DB-0008", "ح / عمالة منتصر السيد (بالطن)", "Montaser El-Sayed labour (per ton)", "labor", "ton", None),
            ("DB-0009", "ح / عمالة محمود السيد (بالطن)", "Mahmoud El-Sayed labour (per ton)", "labor", "ton", None),
            ("DB-0010", "ح / عمالة منازع شافعي (ليلي)", "Manazea Shafei labour (night)", "labor", "ton", None),
            ("DB-0011", "ح / عمالة السيد منتصر (نهاري)", "El-Sayed Montaser labour (day)", "labor", "ton", None),
            ("DB-0012", "ح / إيجار لودر محمد نجيب (إيجار شهري)", "Mohamed Naguib loader hire (monthly)",
             "equipment", "month", None),
            ("DB-0013", "ح / شراء سولار ومشتقات بترولية", "Diesel & petroleum purchases", "fuel", "item", "V-2001"),
            ("DB-0014", "ح / مديونية مشتريات لورشة الصيانة", "Maintenance workshop purchases", "workshop", "item",
             "V-2002"),
            ("DB-0015", "ح / أحمد تاج — مشتريات ومصروفات", "Ahmed Tag — purchases & expenses", "supplies", "item", None),
        ]
        for code, ar, en, kind, unit, vendor_code in DEBT_ACCOUNTS:
            conn.execute(
                """INSERT INTO debt_accounts (company_id, code, name_ar, name_en, kind, billing_unit,
                       vendor_id, expense_account_id, opening_balance_cents, is_active, created_by)
                   VALUES (?,?,?,?,?,?,?,?,0,1,?)""",
                (company_id, code, ar, en, kind, unit, vendors.get(vendor_code) if vendor_code else None,
                 code_to_id["5100"], users.get("ahmed.taranisi")))

        # ---------------- banks ----------------
        conn.execute("""INSERT INTO bank_accounts (company_id, code, name_ar, name_en, account_type, gl_account_id, opening_balance_cents)
                        VALUES (?,?,?,?,?,?,?)""",
                     (company_id, "CASH-01", "صندوق دمياط", "Damietta Cash Box", "cash", code_to_id["1111"], 0))
        conn.execute("""INSERT INTO bank_accounts (company_id, code, name_ar, name_en, account_type, gl_account_id, iban, opening_balance_cents)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (company_id, "BNK-01", "بنك مصر — جاري", "Banque Misr — Current", "bank", code_to_id["1121"],
                      "EG380019000500000000263180002", 0))
        conn.execute("""INSERT INTO bank_accounts (company_id, code, name_ar, name_en, account_type, gl_account_id, iban, opening_balance_cents)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (company_id, "BNK-02", "البنك الأهلي — جاري", "NBE — Current", "bank", code_to_id["1122"],
                      "EG380003000200000000123456789", 0))
        gl_cash = code_to_id["1111"]
        gl_bank1 = code_to_id["1121"]
        gl_bank2 = code_to_id["1122"]

        # ---------------- employees ----------------
        emp_by_code = {}
        for code, ar, en, dept, title, basic, allow, ded, ins, tax in EMPLOYEES:
            cur = conn.execute(
                """INSERT INTO employees (company_id, code, name_ar, name_en, department, job_title, hire_date,
                       basic_salary_cents, allowances_cents, deductions_cents, insurance_cents, tax_cents)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (company_id, code, ar, en, dept, title, f"{YEAR-3}-03-01", basic, allow, ded, ins, tax))
            emp_by_code[code] = cur.lastrowid

        # ---------------- demo-only data (ERP_SEED_MODE=demo) ----------------
        if DEMO:
            # ---------------- fixed assets ----------------
            for code, ar, en, cat, cost, salvage, life, acc in ASSETS:
                conn.execute(
                    """INSERT INTO fixed_assets (company_id, code, name_ar, name_en, category, purchase_date, start_date,
                           cost_cents, salvage_cents, useful_life_months, asset_account_id, dep_account_id, expense_account_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (company_id, code, ar, en, cat, f"{YEAR-1}-01-15", f"{YEAR-1}-02-01", cost, salvage, life,
                     code_to_id[acc], code_to_id["1330"], code_to_id["5320"]))

            # ---------------- opening balances ----------------
            opening_date = f"{YEAR}-01-01"
            opening_stock = [("SKU-001", 4000), ("SKU-002", 5000), ("SKU-003", 8000), ("SKU-004", 10000),
                             ("SKU-005", 3000), ("SKU-006", 1500), ("SKU-008", 4000)]
            stock_value = 0
            for sku, qty in opening_stock:
                p = conn.execute("SELECT * FROM products WHERE id=?", (products[sku],)).fetchone()
                stock_value += int(qty * 1000) * p["purchase_price_cents"] // 1000

            opening_lines = [
                {"account_id": gl_bank1, "debit_cents": 1_250_000_00, "credit_cents": 0, "description": "رصيد افتتاحي بنك مصر"},
                {"account_id": gl_bank2, "debit_cents": 850_000_00, "credit_cents": 0, "description": "رصيد افتتاحي البنك الأهلي"},
                {"account_id": gl_cash, "debit_cents": 120_000_00, "credit_cents": 0, "description": "رصيد افتتاحي الصندوق"},
                {"account_id": code_to_id["1310"], "debit_cents": stock_value, "credit_cents": 0, "description": "مخزون افتتاحي"},
                {"account_id": code_to_id["1210"], "debit_cents": 420_000_00, "credit_cents": 0, "description": "أرصدة عملاء افتتاحية"},
                {"account_id": code_to_id["1430"], "debit_cents": 850_000_00, "credit_cents": 0, "description": "سيارة نقل"},
                {"account_id": code_to_id["1450"], "debit_cents": 240_000_00, "credit_cents": 0, "description": "أثاث مكتبي"},
                {"account_id": code_to_id["1440"], "debit_cents": 180_000_00, "credit_cents": 0, "description": "أجهزة حاسوب"},
                {"account_id": code_to_id["1420"], "debit_cents": 2_400_000_00, "credit_cents": 0, "description": "مبنى المخزن"},
                {"account_id": code_to_id["1330"], "debit_cents": 0, "credit_cents": 320_000_00, "description": "مجمع إهلاك افتتاحي"},
                {"account_id": code_to_id["2110"], "debit_cents": 0, "credit_cents": 560_000_00, "description": "أرصدة موردين افتتاحية"},
                {"account_id": code_to_id["2310"], "debit_cents": 0, "credit_cents": 1_200_000_00, "description": "قرض بنكي"},
            ]
            capital = sum(l["debit_cents"] - l["credit_cents"] for l in opening_lines)
            opening_lines.append({"account_id": code_to_id["3100"], "debit_cents": 0, "credit_cents": capital,
                                  "description": "رأس المال المدفوع"})
            accounting.auto_post_entry(
                conn, ahmed, company_id=company_id, entry_date=opening_date, entry_type="opening",
                memo="الأرصدة الافتتاحية للسنة المالية", lines=opening_lines,
                idempotency_key="opening_balances",
            )
            conn.execute("UPDATE fixed_assets SET accumulated_cents = 32000000")

            # ---------------- opening stock ----------------
            for sku, qty in opening_stock:
                p = conn.execute("SELECT * FROM products WHERE id=?", (products[sku],)).fetchone()
                accounting.stock_in(conn, ahmed, company_id, p["id"], wh["WH-01"], qty * 1000,
                                    p["purchase_price_cents"], opening_date, move_type="in",
                                    notes="مخزون افتتاحي")

            # ---------------- operational data over the last 9 months ----------------
            cust_codes = list(customers.keys())
            vend_codes = list(vendors.keys())
            sale_skus = ["SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005", "SKU-006", "SKU-008"]

            start_month = add_months(month_start(TODAY), -8)
            invoice_counter = 0
            for mi in range(9):
                m = add_months(start_month, mi)
                if m > TODAY:
                    break
                month_days = 28
                # --- purchases first (stock replenishment) ---
                # invoice A: replenishes every stocked SKU early in the month
                inv_date_a = m.replace(day=2)
                lines_a = []
                for sku in sale_skus:
                    pr = conn.execute("SELECT * FROM products WHERE id=?", (products[sku],)).fetchone()
                    lines_a.append({"product_id": pr["id"], "description": pr["name_ar"],
                                    "qty_milli": random.choice([1200, 1500, 1800, 2200]) * 1000,
                                    "unit_price_cents": pr["purchase_price_cents"],
                                    "tax_rate_bp": pr["tax_rate_bp"]})
                v_code = vend_codes[mi % (len(vend_codes) - 1)]
                pid = documents.create_purchase_invoice(conn, mahmoud, company_id, {
                    "vendor_id": vendors[v_code], "invoice_date": inv_date_a.isoformat(),
                    "due_date": (inv_date_a + timedelta(days=30)).isoformat(),
                    "warehouse_id": wh["WH-01"], "supplier_ref": f"SUP-{m.strftime('%Y%m')}-A",
                    "po_number": f"PO-{m.strftime('%y%m')}-A", "grn_number": f"GRN-{m.strftime('%y%m')}-A",
                    "branch_id": br_main, "lines": lines_a,
                })
                documents.post_purchase_invoice(conn, mahmoud, company_id, pid)

                # invoice B: a second supplier mid-month
                inv_date_b = m.replace(day=12)
                lines_b = []
                for sku in random.sample(sale_skus, 3):
                    pr = conn.execute("SELECT * FROM products WHERE id=?", (products[sku],)).fetchone()
                    lines_b.append({"product_id": pr["id"], "description": pr["name_ar"],
                                    "qty_milli": random.choice([800, 1000, 1400]) * 1000,
                                    "unit_price_cents": pr["purchase_price_cents"],
                                    "tax_rate_bp": pr["tax_rate_bp"]})
                v_code_b = vend_codes[(mi + 2) % (len(vend_codes) - 1)]
                pid_b = documents.create_purchase_invoice(conn, mahmoud, company_id, {
                    "vendor_id": vendors[v_code_b], "invoice_date": inv_date_b.isoformat(),
                    "due_date": (inv_date_b + timedelta(days=45)).isoformat(),
                    "warehouse_id": wh["WH-01"], "supplier_ref": f"SUP-{m.strftime('%Y%m')}-B",
                    "po_number": f"PO-{m.strftime('%y%m')}-B", "grn_number": f"GRN-{m.strftime('%y%m')}-B",
                    "branch_id": br_main, "lines": lines_b,
                })
                documents.post_purchase_invoice(conn, mahmoud, company_id, pid_b)
                invoice_counter += 2

                # --- sales ---
                for s in range(6):
                    c_code = cust_codes[(mi * 6 + s) % (len(cust_codes) - 1)]
                    day = 4 + s * 4
                    inv_date = m.replace(day=min(day, month_days))
                    lines = []
                    for _ in range(random.randint(1, 3)):
                        sku = random.choice(sale_skus)
                        p = conn.execute("SELECT * FROM products WHERE id=?", (products[sku],)).fetchone()
                        qty = random.choice([300, 500, 800, 1200])
                        lines.append({"product_id": p["id"], "description": p["name_ar"],
                                      "qty_milli": qty * 1000, "unit_price_cents": p["sale_price_cents"],
                                      "tax_rate_bp": p["tax_rate_bp"]})
                    terms = conn.execute("SELECT payment_terms_days FROM customers WHERE id=?",
                                         (customers[c_code],)).fetchone()["payment_terms_days"]
                    iid = documents.create_sales_invoice(conn, tarek, company_id, {
                        "customer_id": customers[c_code], "invoice_date": inv_date.isoformat(),
                        "due_date": (inv_date + timedelta(days=terms or 0)).isoformat(),
                        "warehouse_id": wh["WH-01"], "branch_id": br_main, "lines": lines,
                    })
                    documents.post_sales_invoice(conn, tarek, company_id, iid)

                    # partial / full collection for older months
                    inv = conn.execute("SELECT * FROM sales_invoices WHERE id=?", (iid,)).fetchone()
                    if mi < 7 or random.random() < 0.5:
                        ratio = 1.0 if (mi < 5 or random.random() < 0.6) else random.choice([0.3, 0.5, 0.7])
                        amount = int(inv["total_cents"] * ratio)
                        if amount > 0:
                            pay_date = min(TODAY, inv_date + timedelta(days=random.choice([10, 20, 30])))
                            pay_id = documents.create_payment(conn, mona, company_id, {
                                "payment_type": "receipt", "payment_date": pay_date.isoformat(),
                                "party_type": "customer", "party_id": inv["customer_id"],
                                "method": "bank", "gl_account_id": gl_bank1, "amount_cents": amount,
                                "branch_id": br_main,
                                "reference": f"تحصيل فاتورة {inv['invoice_no']}",
                                "allocations": [{"invoice_kind": "sales", "invoice_id": iid, "amount_cents": amount}],
                            })
                            documents.post_payment(conn, mona, company_id, pay_id)

                # --- vendor payments on older invoices ---
                open_pis = conn.execute(
                    """SELECT * FROM purchase_invoices WHERE company_id=? AND status IN ('posted','partially_paid')
                       AND invoice_date < ?""",
                    (company_id, (m - timedelta(days=15)).isoformat())).fetchall()
                for pi in open_pis[:2]:
                    remaining = pi["total_cents"] - pi["paid_cents"]
                    if remaining <= 0:
                        continue
                    amount = remaining if random.random() < 0.7 else int(remaining * 0.5)
                    pay_id = documents.create_payment(conn, mona, company_id, {
                        "payment_type": "payment", "payment_date": min(TODAY, m.replace(day=25)).isoformat(),
                        "party_type": "vendor", "party_id": pi["vendor_id"], "method": "bank",
                        "gl_account_id": gl_bank1, "amount_cents": amount, "branch_id": br_main,
                        "reference": f"سداد فاتورة {pi['invoice_no']}",
                        "allocations": [{"invoice_kind": "purchase", "invoice_id": pi["id"], "amount_cents": amount}],
                    })
                    documents.post_payment(conn, mona, company_id, pay_id)

                # --- operating expenses (rent, utilities, comms, marketing) ---
                exp_plan = [
                    ("5300", 45_000_00, "إيجار المقر الرئيسي"),
                    ("5310", 8_500_00, "كهرباء ومياه"),
                    ("5350", 3_200_00, "اتصالات وإنترنت"),
                    ("5330", 12_000_00, "حملة تسويق"),
                    ("5360", 7_500_00, "نقل وشحن"),
                ]
                for code, amount, memo in exp_plan:
                    exp_date = m.replace(day=min(28, month_days))
                    accounting.auto_post_entry(
                        conn, mona, company_id=company_id, entry_date=exp_date.isoformat(),
                        entry_type="expense", memo=f"{memo} — {m.strftime('%m/%Y')}",
                        cost_center_id=cc["CC-ADM"] if code in ("5300", "5310", "5350") else cc["CC-SAL"],
                        lines=[
                            {"account_id": code_to_id[code], "debit_cents": amount, "credit_cents": 0, "description": memo},
                            {"account_id": gl_bank2, "debit_cents": 0, "credit_cents": amount, "description": memo},
                        ],
                        idempotency_key=f"expense:{code}:{m.isoformat()}",
                    )

                # --- bank charges ---
                accounting.auto_post_entry(
                    conn, mona, company_id=company_id, entry_date=m.replace(day=min(27, month_days)).isoformat(),
                    entry_type="expense", memo=f"مصروفات بنكية — {m.strftime('%m/%Y')}",
                    lines=[
                        {"account_id": code_to_id["5400"], "debit_cents": 1_250_00, "credit_cents": 0,
                         "description": "مصروفات إدارية بنكية"},
                        {"account_id": gl_bank1, "debit_cents": 0, "credit_cents": 1_250_00, "description": "خصم بنكي"},
                    ],
                    idempotency_key=f"bank_charges:{m.isoformat()}",
                )

                # --- payroll for months completed ---
                if m < month_start(TODAY):
                    documents.payroll_run(conn, mahmoud, company_id, m.strftime("%Y-%m"))

            # --- quarterly depreciation runs ---
            for q in range(4):
                start = date(YEAR, 3 * q + 1, 1)
                end = (add_months(start, 1) - timedelta(days=1))
                if start > TODAY:
                    break
                documents.depreciation_run(conn, mahmoud, company_id, start.isoformat(), end.isoformat())

            # --- budgets for current year ---
            budget_rows = [
                ("5300", 45_000_00), ("5310", 9_000_00), ("5200", 155_000_00),
                ("5330", 15_000_00), ("5350", 3_500_00), ("5360", 8_000_00), ("5340", 20_000_00),
            ]
            for code, amount in budget_rows:
                for month in range(1, 13):
                    conn.execute(
                        """INSERT OR IGNORE INTO budgets (company_id, fiscal_year_id, account_id, cost_center_id,
                               period_month, budget_cents) VALUES (?,?,?,?,?,?)""",
                        (company_id, fy_current, code_to_id[code], cc["CC-ADM"], month, amount))
            for month in range(1, 13):
                conn.execute(
                    """INSERT OR IGNORE INTO budgets (company_id, fiscal_year_id, account_id, cost_center_id,
                           period_month, budget_cents) VALUES (?,?,?,?,?,?)""",
                    (company_id, fy_current, code_to_id["4100"], cc["CC-SAL"], month, 1_200_000_00))

            # --- approval rules ---
            conn.execute("""INSERT INTO approval_rules (company_id, entity, min_amount_cents, max_amount_cents, required_role, level)
                            VALUES (?,?,?,?,?,1)""", (company_id, "journal_entry", 0, 20_000_000, "chief_accountant"))
            conn.execute("""INSERT INTO approval_rules (company_id, entity, min_amount_cents, max_amount_cents, required_role, level)
                            VALUES (?,?,?,?,?,2)""", (company_id, "journal_entry", 20_000_000, None, "financial_manager"))

            # --- a few entries left pending to demonstrate the approval workflow ---
            pending1 = accounting.create_entry(
                conn, mona, company_id=company_id, entry_date=TODAY.isoformat(), entry_type="manual",
                memo="قيد تسوية مصروفات مستحقة — بانتظار المراجعة",
                lines=[
                    {"account_id": code_to_id["5340"], "debit_cents": 8_400_00, "credit_cents": 0,
                     "description": "مصروفات إدارية مستحقة"},
                    {"account_id": code_to_id["2140"], "debit_cents": 0, "credit_cents": 8_400_00,
                     "description": "استحقاق مصروفات"},
                ])
            conn.execute("UPDATE journal_entries SET status='review', submitted_by=?, submitted_at=? WHERE id=?",
                         (mona["id"], utcnow(), pending1))
            pending2 = accounting.create_entry(
                conn, samy, company_id=company_id, entry_date=TODAY.isoformat(), entry_type="manual",
                memo="قيد شراء أجهزة مكتبية — اعتماد المدير المالي مطلوب",
                lines=[
                    {"account_id": code_to_id["1450"], "debit_cents": 265_000_00, "credit_cents": 0,
                     "description": "أثاث ومكاتب"},
                    {"account_id": code_to_id["2110"], "debit_cents": 0, "credit_cents": 265_000_00,
                     "vendor_id": vendors["V-2003"], "description": "مورد أثاث"},
                ])
            conn.execute("UPDATE journal_entries SET status='approved' WHERE id=?", (pending2,))

        # --- settings ---
        for k, v in [("company_name", "شركة سي سيرفيس للشحن والتفريغ والوكالة الملاحية"),
                     ("default_tax_rate_bp", "1400"),
                     ("approval_threshold_cents", str(accounting.APPROVAL_THRESHOLD_CENTS)),
                     ("inventory_valuation", "weighted_average"),
                     ("fiscal_year_start", f"{YEAR}-01-01")]:
            conn.execute("INSERT INTO settings (company_id, key, value) VALUES (?,?,?)", (company_id, k, v))

        # --- notifications ---
        for uid, t_ar, t_en, body_ar, body_en, typ in [
            (mahmoud["id"], "قيد بانتظار اعتمادك", "Entry awaiting your approval",
             "قيد أثاث ومكاتب بمبلغ 265,000 ج.م تجاوز حد الاعتماد", "Furniture entry EGP 265,000 exceeds threshold", "approval"),
            (samy["id"], "قيد بانتظار المراجعة", "Entry pending review",
             "قيد تسوية مصروفات مستحقة أرسلته منى سليم", "Accrual entry submitted by Mona Selim", "approval"),
            (mahmoud["id"], "فواتير موردين متأخرة", "Overdue vendor invoices",
             "توجد فواتير موردين تجاوزت تاريخ الاستحقاق", "Some vendor invoices passed their due date", "warning"),
            (ahmed["id"], "ملخص الأداء الشهري", "Monthly performance summary",
             "تم تحديث لوحة التحكم بأداء الشهر", "Dashboard refreshed with the month performance", "info"),
        ]:
            conn.execute(
                """INSERT INTO notifications (company_id, user_id, type, title_ar, title_en, body_ar, body_en, link)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (company_id, uid, typ, t_ar, t_en, body_ar, body_en, "#/dashboard"))

        # --- audit trail baseline ---
        accounting.audit(conn, ahmed, "system.seed", "database", None,
                         {"note": "Development seed data created"}, company_id=company_id)

    # write credentials file (dev only) — path is configurable so serverless
    # deployments can write to /tmp instead of the read-only project directory
    out = Path(os.environ.get("ERP_CREDENTIALS_FILE")
               or (Path(__file__).resolve().parent / "data" / "dev-credentials.txt"))
    lines = ["ELTARANISI ERP — بيانات دخول التطوير / DEVELOPMENT LOGIN CREDENTIALS",
             "=" * 62,
             ("كلمات مرور ثابتة لبيئة التطوير فقط — لا تصلح للإنتاج."
              if FIXED_PASSWORDS else
              "كلمات المرور مُنشأة عشوائيًا وقت التهيئة. لا تصلح للإنتاج."),
             ("Fixed demo passwords — DEMO ONLY, never use in production."
              if FIXED_PASSWORDS else
              "Passwords are randomly generated at seed time. NOT for production."),
             "=" * 62, ""]
    for username, name, role, pwd in creds:
        lines.append(f"{name}  |  {username:20s} |  {pwd}  |  {role}")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        # read-only filesystem (e.g. serverless) — the API rebuilds the list
        # from DEV_PASSWORDS + the users table, so this file is optional.
        print(f"credentials file not writable at {out} — skipping (dev API falls back)")

    counts = {}
    with transaction() as conn:
        for t in ("accounts", "journal_entries", "journal_lines", "sales_invoices", "purchase_invoices",
                  "payments", "customers", "vendors", "products", "stock_movements", "fixed_assets",
                  "payroll_runs", "budgets", "audit_logs"):
            counts[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    print("Seed complete:")
    for k, v in counts.items():
        print(f"  {k:22s} {v}")
    print(f"\nCredentials written to: {out}")
    for username, name, role, pwd in creds:
        print(f"  {username:20s} {pwd:24s} ({role})")


if __name__ == "__main__":
    seed()
