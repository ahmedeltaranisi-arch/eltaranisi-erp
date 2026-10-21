"""Integration test suite for Eltaranisi ERP.

Runs against a live server (default http://127.0.0.1:8000) and a seeded database.

    python3 seed.py            # fresh deterministic data
    PORT=8000 python3 server.py &
    python3 tests/test_erp.py

Covers the mandatory acceptance scenarios from the specification:
  1. Sales scenario: invoice → post → partial collection → bank reconciliation →
     verify the effect in GL, trial balance, income statement and balance sheet.
  2. Vendor scenario: purchase invoice → post → partial payment → balance before/after,
     aging, and duplicate-payment protection.
  3. Access control: server-side permission enforcement, segregation of duties,
     posting immutability, closed-period blocking.
  4. Company isolation: users only ever see their own company data.
"""
from __future__ import annotations

import json
import os
import time
import sys
import unittest
import urllib.error
import urllib.request
from decimal import Decimal

BASE = os.environ.get("ERP_BASE", "http://127.0.0.1:8000")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import db  # noqa: E402


def credentials() -> dict:
    path = os.path.join(ROOT, "data", "dev-credentials.txt")
    creds = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 4:
                creds[parts[1]] = {"name": parts[0], "password": parts[2], "role": parts[3]}
    return creds


CREDS = credentials()


class Client:
    def __init__(self, username: str):
        self.token = None
        self.username = username

    def login(self):
        body = {"username": self.username, "password": CREDS[self.username]["password"]}
        status, data = self.request("POST", "/api/auth/login", body, auth=False)
        assert status == 200, (status, data)
        self.token = data["token"]
        return data["user"]

    def request(self, method, path, body=None, auth=True):
        req = urllib.request.Request(BASE + path, method=method)
        req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as resp:
                raw = resp.read().decode()
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body or {})

    def patch(self, path, body=None):
        return self.request("PATCH", path, body or {})


class ERPTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = Client("ahmed.taranisi"); cls.owner.login()
        cls.fm = Client("mahmoud.morad"); cls.fm.login()
        cls.chief = Client("samy.abdelrahman"); cls.chief.login()
        cls.accountant = Client("mona.selim"); cls.accountant.login()
        cls.auditor = Client("heba.elsherif"); cls.auditor.login()
        cls.sales = Client("tarek.adel"); cls.sales.login()
        cls.warehouse = Client("karim.fathy"); cls.warehouse.login()

    # ---------------- helpers ----------------
    def tb(self, client=None, date_from=None, date_to=None):
        c = client or self.owner
        today = json.loads(json.dumps(__import__("datetime").date.today().isoformat()))
        d1 = date_from or today[:4] + "-01-01"
        status, data = c.get(f"/api/reports/trial-balance?from={d1}&to={date_to or today}")
        self.assertEqual(status, 200, data)
        return data

    def account_balance(self, code):
        """Natural presentation balance (assets/expenses debit-positive, the rest credit-positive)."""
        status, data = self.owner.get("/api/accounts")
        self.assertEqual(status, 200, data)
        row = next(a for a in data["accounts"] if a["code"] == code)
        return row["display_balance_cents"]

    def first(self, rows_key, path, predicate=lambda r: True):
        status, data = self.owner.get(path)
        self.assertEqual(status, 200, data)
        return next(r for r in data[rows_key] if predicate(r))

    # ================================================================
    # 0. authentication & identity
    # ================================================================
    def test_01_two_independent_accounts(self):
        self.assertNotEqual(CREDS["ahmed.taranisi"]["password"], CREDS["mahmoud.morad"]["password"])
        status, me = self.owner.get("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(me["user"]["username"], "ahmed.taranisi")
        self.assertEqual(me["user"]["role"], "company_owner")
        status, me2 = self.fm.get("/api/auth/me")
        self.assertEqual(me2["user"]["role"], "financial_manager")
        self.assertNotEqual(me["user"]["id"], me2["user"]["id"])

    def test_02_bad_password_rejected(self):
        c = Client("ahmed.taranisi")
        status, data = c.request("POST", "/api/auth/login",
                                 {"username": "ahmed.taranisi", "password": "definitely-wrong"}, auth=False)
        self.assertEqual(status, 401)
        self.assertIn("INVALID_CREDENTIALS", data["error"])

    def test_03_unauthenticated_request_blocked(self):
        status, _ = Client("ahmed.taranisi").get("/api/dashboard")
        self.assertEqual(status, 401)

    # ================================================================
    # 1. SALES SCENARIO (mandatory)
    # ================================================================
    def test_10_sales_scenario_end_to_end(self):
        ar_before = self.account_balance("1210")
        rev_before = self.account_balance("4100")
        cogs_before = self.account_balance("5100")
        inv_before = self.account_balance("1310")
        cash_before = self.account_balance("1121")

        status, cust = self.owner.get("/api/customers")
        customer = cust["customers"][0]
        status, prods = self.owner.get("/api/products")
        product = next(p for p in prods["products"] if not p["is_service"] and p["stock_value_cents"] > 0)
        status, meta = self.owner.get("/api/meta")
        warehouse_id = meta["warehouses"][0]["id"]

        # --- create + post invoice ---
        status, res = self.sales.post("/api/sales-invoices", {
            "customer_id": customer["id"], "invoice_date": self.today(), "due_date": self.today(),
            "warehouse_id": warehouse_id,
            "lines": [{"product_id": product["id"], "qty": 4, "unit_price": 150, "tax_rate_bp": 1400}],
            "post": True,
        })
        self.assertEqual(status, 200, res)
        invoice_id = res["id"]

        status, detail = self.owner.get(f"/api/sales-invoices/{invoice_id}")
        inv = detail["invoice"]
        self.assertEqual(inv["status"], "posted")
        # 4 * 150 = 600 net, 14% VAT = 84 → 684 total
        self.assertEqual(inv["total_cents"], 68_400)
        self.assertIsNotNone(inv["journal_entry_id"])

        # --- effect on the ledger ---
        self.assertEqual(self.account_balance("1210"), ar_before + 68_400, "AR must increase by invoice total")
        self.assertEqual(self.account_balance("4100"), rev_before + 60_000, "Revenue must increase by net amount")
        vat_out = self.account_balance("2210")
        self.assertGreater(vat_out, 0)
        self.assertGreater(self.account_balance("5100"), cogs_before, "COGS expense must increase")
        self.assertLess(self.account_balance("1310"), inv_before, "Inventory must decrease by cost of goods sold")

        # --- partial collection ---
        banks = self.owner.get("/api/banks")[1]
        gl_bank = next(b["gl_account_id"] for b in banks["accounts"] if b["code"] == "BNK-01")
        status, pay = self.owner.post("/api/payments", {
            "payment_type": "receipt", "payment_date": self.today(), "party_type": "customer",
            "party_id": customer["id"], "method": "bank", "gl_account_id": gl_bank,
            "amount": 300, "post": True,
            "allocations": [{"invoice_kind": "sales", "invoice_id": invoice_id, "amount": 300}],
        })
        self.assertEqual(status, 200, pay)
        status, detail = self.owner.get(f"/api/sales-invoices/{invoice_id}")
        self.assertEqual(detail["invoice"]["status"], "partially_paid")
        self.assertEqual(detail["invoice"]["paid_cents"], 30_000)
        self.assertEqual(self.account_balance("1121"), cash_before + 30_000)
        self.assertEqual(self.account_balance("1210"), ar_before + 68_400 - 30_000)

        # --- bank reconciliation (proposal → human confirmation) ---
        status, txn = self.owner.post("/api/bank-transactions", {
            "bank_account_id": banks["accounts"][1]["id"], "txn_date": self.today(),
            "description": "تحصيل من العميل", "amount": 300, "direction": "debit",
        })
        self.assertEqual(status, 200, txn)
        status, sug = self.owner.get("/api/bank-transactions/suggestions")
        self.assertEqual(status, 200)
        self.assertTrue(any(s["transaction"]["id"] == txn["id"] for s in sug["suggestions"]),
                        "posted payment of the same amount must be suggested")
        status, _ = self.owner.post(f"/api/bank-transactions/{txn['id']}/reconcile",
                                    {"payment_id": pay["id"]})
        self.assertEqual(status, 200)

        # --- statements stay consistent ---
        tb = self.tb()
        self.assertTrue(tb["balanced"], "trial balance must remain balanced after the whole scenario")
        status, bs = self.owner.get(f"/api/reports/balance-sheet?as_of={self.today()}")
        self.assertTrue(bs["balanced"], "assets = liabilities + equity")
        status, inc = self.owner.get(f"/api/reports/income-statement?from={self.year_start()}&to={self.today()}")
        self.assertGreaterEqual(inc["total_revenue_cents"], 60_000)

    def test_11_sales_invoice_cannot_post_twice(self):
        status, cust = self.owner.get("/api/customers")
        status, res = self.owner.post("/api/sales-invoices", {
            "customer_id": cust["customers"][0]["id"], "invoice_date": self.today(),
            "lines": [{"product_id": None, "description": "خدمة توصيل", "qty": 1, "unit_price": 100, "tax_rate_bp": 0}],
        })
        self.assertEqual(status, 200, res)
        status, _ = self.owner.post(f"/api/sales-invoices/{res['id']}/post")
        self.assertEqual(status, 200)
        status, data = self.owner.post(f"/api/sales-invoices/{res['id']}/post")
        self.assertEqual(status, 422)
        self.assertIn("INVALID_STATE", data["error"])

    # ================================================================
    # 2. VENDOR PAYABLES SCENARIO (mandatory)
    # ================================================================
    def test_20_vendor_payables_scenario(self):
        status, vend = self.owner.get("/api/vendors")
        vendor = vend["vendors"][0]
        status, payables = self.owner.get("/api/payables")
        before = next(v for v in payables["vendors"] if v["id"] == vendor["id"])
        ap_before = self.account_balance("2110")

        ref = f"TEST-VENDOR-{int(time.time() * 1000)}"  # unique per run → suite is re-runnable
        status, res = self.owner.post("/api/purchase-invoices", {
            "vendor_id": vendor["id"], "invoice_date": self.today(), "due_date": self.today(),
            "supplier_ref": ref, "po_number": "PO-TEST", "grn_number": "GRN-TEST",
            "lines": [{"product_id": None, "description": "توريد مستلزمات", "qty": 1,
                       "unit_price": 1000, "tax_rate_bp": 1400}],
            "post": True,
        })
        self.assertEqual(status, 200, res)
        invoice_id = res["id"]

        status, detail = self.owner.get(f"/api/purchase-invoices/{invoice_id}")
        self.assertEqual(detail["invoice"]["status"], "posted")
        self.assertEqual(detail["invoice"]["total_cents"], 114_000)  # 1000 + 14%
        self.assertEqual(detail["invoice"]["three_way_match"], 1)

        status, payables = self.owner.get("/api/payables")
        after = next(v for v in payables["vendors"] if v["id"] == vendor["id"])
        self.assertEqual(after["outstanding_cents"], before["outstanding_cents"] + 114_000,
                         "vendor outstanding must increase by the invoice total")
        self.assertEqual(self.account_balance("2110"), ap_before + 114_000,
                         "AP control account must match vendor subledger")

        # duplicate vendor reference is rejected
        status, dup = self.owner.post("/api/purchase-invoices", {
            "vendor_id": vendor["id"], "invoice_date": self.today(),
            "supplier_ref": ref,
            "lines": [{"product_id": None, "description": "مكرر", "qty": 1, "unit_price": 10, "tax_rate_bp": 0}],
        })
        self.assertEqual(status, 422)
        self.assertIn("DUPLICATE", dup["error"])

        # partial payment
        banks = self.owner.get("/api/banks")[1]
        gl_bank = next(b["gl_account_id"] for b in banks["accounts"] if b["code"] == "BNK-01")
        status, pay = self.owner.post("/api/payments", {
            "payment_type": "payment", "payment_date": self.today(), "party_type": "vendor",
            "party_id": vendor["id"], "method": "bank", "gl_account_id": gl_bank,
            "amount": 500, "post": True,
            "allocations": [{"invoice_kind": "purchase", "invoice_id": invoice_id, "amount": 500}],
        })
        self.assertEqual(status, 200, pay)
        status, payables = self.owner.get("/api/payables")
        paid = next(v for v in payables["vendors"] if v["id"] == vendor["id"])
        self.assertEqual(paid["outstanding_cents"], after["outstanding_cents"] - 50_000)
        self.assertEqual(self.account_balance("2110"), ap_before + 114_000 - 50_000)

        status, detail = self.owner.get(f"/api/purchase-invoices/{invoice_id}")
        self.assertEqual(detail["invoice"]["status"], "partially_paid")

        # over-allocation is refused (prevents paying the same invoice twice)
        status, over = self.owner.post("/api/payments", {
            "payment_type": "payment", "payment_date": self.today(), "party_type": "vendor",
            "party_id": vendor["id"], "method": "bank", "gl_account_id": gl_bank,
            "amount": 10_000, "post": True,
            "allocations": [{"invoice_kind": "purchase", "invoice_id": invoice_id, "amount": 10_000}],
        })
        self.assertEqual(status, 422)
        self.assertIn("OVER_ALLOCATION", over["error"])

    def test_21_vendor_aging_matches_open_invoices(self):
        status, aging = self.owner.get(f"/api/reports/aging?kind=purchase&as_of={self.today()}")
        self.assertEqual(status, 200)
        status, payables = self.owner.get("/api/payables")
        open_total = sum(i["remaining_cents"] for i in aging["items"])
        self.assertEqual(open_total, sum(aging["buckets"].values()))
        self.assertEqual(payables["totals"]["outstanding_cents"] >= 0, True)
        self.assertEqual(payables["totals"]["journal_balance_cents"], payables["totals"]["outstanding_cents"],
                         "vendor subledger must reconcile with the AP control account")

    # ================================================================
    # 3. PERMISSIONS, WORKFLOW & IMMUTABILITY
    # ================================================================
    def test_30_role_permissions_enforced_server_side(self):
        # auditor is read-only
        status, data = self.auditor.post("/api/journal-entries", {
            "memo": "محاولة", "lines": [{"account_id": 1, "debit": 10, "credit": 0}]})
        self.assertEqual(status, 403)
        # auditor can read reports
        self.assertEqual(self.auditor.get("/api/reports/trial-balance")[0], 200)
        # sales user cannot create journal entries or approve
        self.assertEqual(self.sales.post("/api/journal-entries", {
            "memo": "x", "lines": [{"account_id": 1, "debit": 1, "credit": 0}]})[0], 403)
        # accountant cannot post an entry (no journal.post permission)
        accounts = self.owner.get("/api/accounts")[1]["accounts"]
        deb = next(a for a in accounts if a["is_postable"] and a["type"] == "expense")
        crd = next(a for a in accounts if a["is_postable"] and a["type"] == "asset")
        status, created = self.accountant.post("/api/journal-entries", {
            "memo": "قيد اختبار الصلاحيات",
            "lines": [{"account_id": deb["id"], "debit": 100, "credit": 0},
                      {"account_id": crd["id"], "debit": 0, "credit": 100}]})
        self.assertEqual(status, 200, created)
        self.assertEqual(self.accountant.post(f"/api/journal-entries/{created['id']}/post")[0], 403)

    def test_31_segregation_of_duties(self):
        accounts = self.owner.get("/api/accounts")[1]["accounts"]
        deb = next(a for a in accounts if a["is_postable"] and a["type"] == "expense")
        crd = next(a for a in accounts if a["is_postable"] and a["type"] == "asset")
        status, created = self.chief.post("/api/journal-entries", {
            "memo": "قيد فصل المهام",
            "lines": [{"account_id": deb["id"], "debit": 50, "credit": 0},
                      {"account_id": crd["id"], "debit": 0, "credit": 50}]})
        self.assertEqual(status, 200)
        # the creator cannot review / approve their own entry
        status, data = self.chief.post(f"/api/journal-entries/{created['id']}/submit")
        self.assertEqual(status, 422)
        self.assertIn("SOD_VIOLATION", data["error"])

    def test_32_workflow_and_posting_rules(self):
        accounts = self.owner.get("/api/accounts")[1]["accounts"]
        deb = next(a for a in accounts if a["is_postable"] and a["type"] == "expense")
        crd = next(a for a in accounts if a["is_postable"] and a["type"] == "asset")
        status, created = self.accountant.post("/api/journal-entries", {
            "memo": "قيد دورة اعتماد كاملة",
            "lines": [{"account_id": deb["id"], "debit": 250, "credit": 0},
                      {"account_id": crd["id"], "debit": 0, "credit": 250}]})
        self.assertEqual(status, 200)
        # posting before approval is refused
        status, data = self.owner.post(f"/api/journal-entries/{created['id']}/post")
        self.assertEqual(status, 422)
        self.assertIn("NOT_APPROVED", data["error"])
        # submit (by another user) → review → post
        self.assertEqual(self.chief.post(f"/api/journal-entries/{created['id']}/submit")[0], 200)
        self.assertEqual(self.fm.post(f"/api/journal-entries/{created['id']}/review",
                                      {"decision": "approve"})[0], 200)
        self.assertEqual(self.fm.post(f"/api/journal-entries/{created['id']}/post")[0], 200)
        # posted entries are immutable: they can only be reversed
        status, data = self.owner.post(f"/api/journal-entries/{created['id']}/post")
        self.assertEqual(status, 422)
        status, rev = self.owner.post(f"/api/journal-entries/{created['id']}/reverse",
                                      {"reason": "اختبار العكس"})
        self.assertEqual(status, 200, rev)
        status, original = self.owner.get(f"/api/journal-entries/{created['id']}")
        self.assertEqual(original["entry"]["status"], "reversed")
        status, mirror = self.owner.get(f"/api/journal-entries/{rev['reversal_entry_id']}")
        self.assertEqual(mirror["entry"]["status"], "posted")
        # the reversal zeroes the net effect
        self.assertEqual(original["entry"]["total_debit_cents"], mirror["entry"]["total_debit_cents"])

    def test_33_unbalanced_entry_rejected(self):
        accounts = self.owner.get("/api/accounts")[1]["accounts"]
        deb = next(a for a in accounts if a["is_postable"] and a["type"] == "expense")
        crd = next(a for a in accounts if a["is_postable"] and a["type"] == "asset")
        status, data = self.owner.post("/api/journal-entries", {
            "memo": "قيد غير متوازن",
            "lines": [{"account_id": deb["id"], "debit": 100, "credit": 0},
                      {"account_id": crd["id"], "debit": 0, "credit": 90}]})
        self.assertEqual(status, 422)
        self.assertIn("UNBALANCED", data["error"])

    def test_34_cannot_post_to_group_account(self):
        accounts = self.owner.get("/api/accounts")[1]["accounts"]
        group = next(a for a in accounts if not a["is_postable"])
        leaf = next(a for a in accounts if a["is_postable"])
        status, data = self.owner.post("/api/journal-entries", {
            "memo": "ترحيل إلى حساب تجميعي",
            "lines": [{"account_id": group["id"], "debit": 10, "credit": 0},
                      {"account_id": leaf["id"], "debit": 0, "credit": 10}]})
        self.assertEqual(status, 422)
        self.assertIn("NON_POSTABLE_ACCOUNT", data["error"])

    def test_35_closed_period_blocks_posting(self):
        periods = self.owner.get("/api/fiscal-periods")[1]["periods"]
        closed = next(p for p in periods if p["status"] == "closed")
        accounts = self.owner.get("/api/accounts")[1]["accounts"]
        deb = next(a for a in accounts if a["is_postable"] and a["type"] == "expense")
        crd = next(a for a in accounts if a["is_postable"] and a["type"] == "asset")
        status, data = self.owner.post("/api/journal-entries", {
            "entry_date": closed["start_date"], "memo": "ترحيل في فترة مقفلة",
            "lines": [{"account_id": deb["id"], "debit": 10, "credit": 0},
                      {"account_id": crd["id"], "debit": 0, "credit": 10}]})
        self.assertEqual(status, 422)
        self.assertIn("PERIOD_CLOSED", data["error"])

    # ================================================================
    # 4. DATA ISOLATION BETWEEN COMPANIES
    # ================================================================
    def test_40_company_isolation(self):
        """A second company is created directly in the DB; its users must never
        see the first company's data through the API."""
        import security
        with db.transaction() as conn:
            org = conn.execute("SELECT id FROM organizations").fetchone()["id"]
            conn.execute("""INSERT OR IGNORE INTO companies (org_id, code, name_ar, name_en, base_currency)
                            VALUES (?,?,?,?,?)""",
                         (org, "ISO-02", "شركة العزل", "Isolation Co.", "EGP"))
            cid = conn.execute("SELECT id FROM companies WHERE code='ISO-02'").fetchone()["id"]
            # clean previous run so the suite can be executed repeatedly on one database
            conn.execute("DELETE FROM fiscal_periods WHERE company_id=?", (cid,))
            conn.execute("DELETE FROM fiscal_years WHERE company_id=?", (cid,))
            fy = conn.execute("""INSERT INTO fiscal_years (company_id, name, start_date, end_date)
                                 VALUES (?,?,?,?)""",
                              (cid, "FY-ISO", self.year_start(), self.year_start()[:4] + "-12-31")).lastrowid
            from datetime import date
            m_start = date.today().replace(day=1)
            conn.execute("""INSERT INTO fiscal_periods (company_id, fiscal_year_id, period_no, start_date, end_date)
                            VALUES (?,?,1,?,?)""", (cid, fy, m_start.isoformat(), self.today()))
            conn.execute("""INSERT OR IGNORE INTO roles (code, name_ar, name_en, permissions, data_scope)
                            SELECT code, name_ar, name_en, permissions, data_scope FROM roles WHERE code='accountant'""")
            # Upsert (do NOT use INSERT OR REPLACE: deleting the user would break FKs)
            conn.execute("""INSERT INTO users (company_id, username, full_name_ar, full_name_en, email,
                              password_hash, role_code, language, theme, password_must_change)
                            VALUES (?,?,?,?,?,?,?,?,?,0)
                            ON CONFLICT(username) DO UPDATE SET
                              company_id=excluded.company_id,
                              password_hash=excluded.password_hash,
                              role_code=excluded.role_code,
                              is_active=1, failed_attempts=0, locked_until=NULL""",
                         (cid, "iso.user", "مستخدم العزل", "Isolation User", "iso@example.com",
                          security.hash_password("Isolation#2026"), "accountant", "ar", "light"))
        iso = Client("iso.user")
        iso.token = None
        status, data = iso.request("POST", "/api/auth/login",
                                   {"username": "iso.user", "password": "Isolation#2026"}, auth=False)
        self.assertEqual(status, 200, data)
        iso.token = data["token"]
        self.assertNotEqual(data["user"]["company_id"], self.owner.get("/api/auth/me")[1]["user"]["company_id"])
        status, cust = iso.get("/api/customers")
        self.assertEqual(status, 200)
        self.assertEqual(cust["customers"], [], "new company must not see another company's customers")
        status, payables = iso.get("/api/payables")
        self.assertEqual(payables["vendors"], [])
        self.assertEqual(payables["totals"]["outstanding_cents"], 0)
        status, tb = iso.get(f"/api/reports/trial-balance?from={self.year_start()}&to={self.today()}")
        self.assertTrue(tb["balanced"])
        self.assertEqual(tb["totals"]["period_debit"], 0)

    # ================================================================
    # 5. REPORTING & EXPORTS
    # ================================================================
    def test_50_financial_statements_internally_consistent(self):
        tb = self.tb()
        status, inc = self.owner.get(f"/api/reports/income-statement?from={self.year_start()}&to={self.today()}")
        status, bs = self.owner.get(f"/api/reports/balance-sheet?as_of={self.today()}")
        # revenue in the income statement equals the credit movement of revenue accounts
        revenue_accounts = {r["code"] for r in tb["rows"] if r["type"] == "revenue"}
        revenue_from_tb = sum(r["period_credit"] - r["period_debit"] for r in tb["rows"] if r["code"] in revenue_accounts)
        self.assertEqual(inc["total_revenue_cents"], revenue_from_tb)
        self.assertTrue(bs["balanced"])
        self.assertEqual(bs["total_assets_cents"], bs["total_liabilities_cents"] + bs["total_equity_cents"])

    def test_51_csv_export(self):
        req = urllib.request.Request(BASE + "/api/reports/export?kind=trial-balance")
        req.add_header("Authorization", "Bearer " + self.owner.token)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            self.assertIn("text/csv", resp.headers.get("Content-Type"))
        self.assertTrue(body.startswith("\ufeff"), "CSV must include a UTF-8 BOM so Excel renders Arabic")
        self.assertIn("Code", body.splitlines()[0])

    def test_52_export_requires_permission(self):
        # sales_user has no reports.export permission
        status, _ = self.sales.get("/api/reports/export?kind=trial-balance")
        self.assertEqual(status, 403)

    # ================================================================
    # 6. AI ASSISTANT SCOPE
    # ================================================================
    def test_60_assistant_respects_permissions_and_data(self):
        status, data = self.owner.post("/api/assistant/ask", {"question": "ما إجمالي مديونية الموردين؟"})
        self.assertEqual(status, 200, data)
        self.assertIn("answer", data)
        self.assertEqual(data["source"], "vendor_payables")
        status, payables = self.owner.get("/api/payables")
        self.assertIn(f"{payables['totals']['outstanding_cents']/100:,.2f}", data["answer"])
        # a role without ai.use cannot call the assistant
        status, _ = self.sales.post("/api/assistant/ask", {"question": "السيولة؟"})
        self.assertEqual(status, 403)

    # ================================================================
    # 7. AUDIT TRAIL
    # ================================================================
    def test_70_audit_trail_records_actions(self):
        status, logs = self.owner.get("/api/audit-logs?limit=300")
        self.assertEqual(status, 200)
        actions = {l["action"] for l in logs["logs"]}
        self.assertIn("auth.login", actions)
        self.assertIn("journal.post", actions)
        self.assertIn("sales_invoice.post", actions)
        self.assertIn("payment.post", actions)

    def test_71_audit_log_requires_permission(self):
        status, _ = self.sales.get("/api/audit-logs")
        self.assertEqual(status, 403)

    # ---------------- date helpers ----------------
    @staticmethod
    def today():
        from datetime import date
        return date.today().isoformat()

    @classmethod
    def year_start(cls):
        from datetime import date
        return date.today().replace(month=1, day=1).isoformat()



    # ================================================================
    # 4. DOCUMENT EXPORTS (PDF / Excel / E-invoice readiness)
    # ================================================================
    def _raw(self, path, client=None):
        c = client or self.owner
        req = urllib.request.Request(BASE + path)
        req.add_header("Authorization", "Bearer " + c.token)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_50_invoice_pdf_exports(self):
        inv = self.owner.get("/api/sales-invoices")[1]["invoices"][0]
        for lang in ("ar", "en"):
            status, body, headers = self._raw(f"/api/sales-invoices/{inv['id']}/pdf?lang={lang}")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Content-Type"), "application/pdf")
            self.assertTrue(body.startswith(b"%PDF-1.7"), "must be a real PDF")
            self.assertGreater(len(body), 150_000, "font must be embedded")
            self.assertIn(b"/FontFile2", body, "Arabic font must be embedded")
            self.assertIn(b"/ToUnicode", body, "text must be selectable/indexable")

    def test_51_invoice_xlsx_exports(self):
        inv = self.owner.get("/api/purchase-invoices")[1]["invoices"][0]
        status, body, headers = self._raw(f"/api/purchase-invoices/{inv['id']}/xlsx")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"PK"), "xlsx must be a zip container")
        import io, zipfile
        z = zipfile.ZipFile(io.BytesIO(body))
        names = z.namelist()
        for part in ("xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml"):
            self.assertIn(part, names)
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('rightToLeft="1"', z.read("xl/workbook.xml").decode(),
                      "Arabic workbooks open right-to-left")

    def test_52_einvoice_readiness_is_stable_and_logged(self):
        inv = self.owner.get("/api/sales-invoices")[1]["invoices"][0]
        status, first = self.owner.get(f"/api/sales-invoices/{inv['id']}/einvoice")
        self.assertEqual(status, 200)
        self.assertEqual(len(first["documentUuid"]), 32)
        self.assertTrue(first["digest"].startswith("sha256:"))
        self.assertFalse(first["readiness"]["signed"], "documents are never auto-signed/posted")
        status, second = self.owner.get(f"/api/sales-invoices/{inv['id']}/einvoice")
        self.assertEqual(first["documentUuid"], second["documentUuid"],
                         "the e-invoice UUID must be stable (immutable record)")
        # persistence + audit trail
        row = db.read().execute("SELECT * FROM einvoice_documents WHERE invoice_kind='sales' AND invoice_id=?",
                                (inv["id"],)).fetchone()
        self.assertIsNotNone(row)
        # the audit trail is append-only and shared, so assert on the row itself
        # instead of a "most recent N" window that other tests can push out.
        audit_row = db.read().execute(
            "SELECT action, entity, entity_id FROM audit_logs WHERE action='einvoice.issue' "
            "AND entity='sales_invoice' AND entity_id=? ORDER BY id DESC LIMIT 1", (inv["id"],)).fetchone()
        self.assertIsNotNone(audit_row, "issuing an e-invoice document must be audited")

    def test_53_report_pdf_and_excel(self):
        for kind in ("trial-balance", "income-statement", "balance-sheet", "vendor-payables"):
            status, body, headers = self._raw(f"/api/reports/pdf?kind={kind}&lang=ar")
            self.assertEqual(status, 200, kind)
            self.assertTrue(body.startswith(b"%PDF-1.7"), kind)
            status, body, _ = self._raw(f"/api/reports/xlsx?kind={kind}" if kind in
                                        ("trial-balance", "vendor-payables") else "/api/reports/pdf?kind=" + kind)
            self.assertEqual(status, 200, kind)

    def test_54_exports_require_permission(self):
        inv = self.owner.get("/api/sales-invoices")[1]["invoices"][0]
        status, _, _ = self._raw(f"/api/sales-invoices/{inv['id']}/pdf", client=self.warehouse)
        self.assertEqual(status, 403, "stock keeper must not read accounting documents")
        status, _, _ = self._raw("/api/reports/pdf?kind=trial-balance", client=self.warehouse)
        self.assertEqual(status, 403)
        status, _, _ = self._raw("/api/purchase-invoices/1/pdf", client=self.sales)
        self.assertEqual(status, 403, "sales role must not read purchase documents")

    # ================================================================
    # 5. COMPANY SETTINGS (invoice identity)
    # ================================================================
    def test_55_company_settings_roundtrip(self):
        status, data = self.owner.get("/api/company")
        self.assertEqual(status, 200)
        before = data["company"]["name_ar"]
        original = data["settings"].get("invoice_footer", "")
        status, res = self.owner.patch("/api/company", {"invoice_footer": "اختبار التذييل"})
        self.assertEqual(status, 200, res)
        status, data = self.owner.get("/api/company")
        self.assertEqual(data["settings"]["invoice_footer"], "اختبار التذييل")
        self.assertEqual(data["company"]["name_ar"], before, "partial update must not wipe other fields")
        self.assertEqual(data["company"]["tax_number"], "512-345-678")
        # restore so the seeded invoice footer is not left polluted by the test
        self.owner.patch("/api/company", {"invoice_footer": original})

    def test_56_company_settings_need_permission(self):
        status, res = self.accountant.patch("/api/company", {"name_ar": "محاولة"})
        self.assertEqual(status, 403)

    # ================================================================
    # 6. DEMO ACCOUNTS ENDPOINT (development only)
    # ================================================================
    def test_57_demo_accounts_endpoint(self):
        req = urllib.request.Request(BASE + "/api/dev/accounts")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
        users = {a["username"] for a in data["accounts"]}
        self.assertIn("ahmed.taranisi", users)
        self.assertIn("mahmoud.morad", users)
        self.assertEqual(len(users), 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
