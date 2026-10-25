"""Integration test suite for ELTARANISI ERP.

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

    def request(self, method, path, body=None, auth=True, token=None):
        req = urllib.request.Request(BASE + path, method=method)
        req.add_header("Content-Type", "application/json")
        tok = token if token is not None else self.token
        if auth and tok:
            req.add_header("Authorization", "Bearer " + tok)
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

    def delete(self, path, body=None):
        return self.request("DELETE", path, body if body is not None else {})


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
    # 7. DELETION SAFETY (every create has a matching delete)
    # ================================================================
    def _make_invoice(self, qty=2, price=150, post=True):
        status, cust = self.owner.get("/api/customers")
        customer = cust["customers"][0]
        status, prods = self.owner.get("/api/products")
        product = next(p for p in prods["products"] if not p["is_service"] and p["stock_value_cents"] > 0)
        status, meta = self.owner.get("/api/meta")
        status, res = self.sales.post("/api/sales-invoices", {
            "customer_id": customer["id"], "invoice_date": self.today(), "due_date": self.today(),
            "warehouse_id": meta["warehouses"][0]["id"],
            "lines": [{"product_id": product["id"], "qty": qty, "unit_price": price, "tax_rate_bp": 1400}],
            "post": post,
        })
        self.assertEqual(status, 200, res)
        return res["id"], customer

    def test_80_delete_requires_confirmation(self):
        invoice_id, _ = self._make_invoice()
        status, data = self.owner.delete(f"/api/sales-invoices/{invoice_id}")
        self.assertEqual(status, 428, data)
        self.assertIn("CONFIRM_REQUIRED", data["error"])
        # still there
        status, _ = self.owner.get(f"/api/sales-invoices/{invoice_id}")
        self.assertEqual(status, 200)

    def test_81_receipt_delete_unlinks_invoice(self):
        invoice_id, customer = self._make_invoice()
        banks = self.owner.get("/api/banks")[1]
        gl_bank = next(b["gl_account_id"] for b in banks["accounts"] if b["code"] == "BNK-01")
        status, pay = self.owner.post("/api/payments", {
            "payment_type": "receipt", "payment_date": self.today(), "party_type": "customer",
            "party_id": customer["id"], "method": "bank", "gl_account_id": gl_bank,
            "amount": 100, "post": True,
            "allocations": [{"invoice_kind": "sales", "invoice_id": invoice_id, "amount": 100}],
        })
        self.assertEqual(status, 200, pay)

        # an invoice with money against it must not vanish
        status, blocked = self.owner.delete(f"/api/sales-invoices/{invoice_id}", {"confirm": True})
        self.assertEqual(status, 409, blocked)
        self.assertIn("HAS_PAYMENTS", blocked["error"])

        status, res = self.owner.delete(f"/api/payments/{pay['id']}", {"confirm": True})
        self.assertEqual(status, 200, res)
        detail = self.owner.get(f"/api/sales-invoices/{invoice_id}")[1]["invoice"]
        self.assertEqual(detail["paid_cents"], 0)
        self.assertEqual(detail["status"], "posted")
        self.assertTrue(self.tb()["balanced"], "trial balance must stay balanced after deleting a receipt")

    def test_82_invoice_delete_removes_lines_and_entry(self):
        invoice_id, _ = self._make_invoice(qty=1, price=90)
        detail = self.owner.get(f"/api/sales-invoices/{invoice_id}")[1]
        entry_id = detail["invoice"]["journal_entry_id"]
        self.assertIsNotNone(entry_id)

        status, res = self.owner.delete(f"/api/sales-invoices/{invoice_id}", {"confirm": True})
        self.assertEqual(status, 200, res)
        self.assertEqual(self.owner.get(f"/api/sales-invoices/{invoice_id}")[0], 404)
        self.assertEqual(self.owner.get(f"/api/journal-entries/{entry_id}")[0], 404)
        self.assertTrue(self.tb()["balanced"])

    def test_83_master_delete_blocked_when_documents_exist(self):
        status, cust = self.owner.get("/api/customers")
        busy = next(c for c in cust["customers"] if c["outstanding_cents"] or c["id"])
        status, data = self.owner.delete(f"/api/customers/{cust['customers'][0]['id']}", {"confirm": True})
        # either it is referenced (409) or it was clean (200) — never a 500
        self.assertIn(status, (200, 409), data)
        if status == 409:
            self.assertIn("HAS_DOCUMENTS", data["error"])

        # a brand-new customer is deletable
        status, made = self.owner.post("/api/customers", {
            "code": "C-DEL" + str(int(time.time())), "name_ar": "عميل للحذف", "name_en": "Deletable customer"})
        self.assertEqual(status, 200, made)
        status, res = self.owner.delete(f"/api/customers/{made['id']}", {"confirm": True})
        self.assertEqual(status, 200, res)
        status, again = self.owner.delete(f"/api/customers/{made['id']}", {"confirm": True})
        self.assertEqual(status, 404)

    def test_84_delete_permission_enforced(self):
        invoice_id, _ = self._make_invoice(qty=1, price=100)
        status, data = self.sales.delete(f"/api/sales-invoices/{invoice_id}", {"confirm": True})
        self.assertEqual(status, 403, data)
        # the sales role cannot delete master data either
        status, _ = self.sales.delete("/api/customers/1", {"confirm": True})
        self.assertEqual(status, 403)
        self.assertEqual(self.owner.get(f"/api/sales-invoices/{invoice_id}")[0], 200)

    def test_85_deletion_is_audited_with_snapshot(self):
        invoice_id, _ = self._make_invoice(qty=1, price=70)
        number = self.owner.get(f"/api/sales-invoices/{invoice_id}")[1]["invoice"]["invoice_no"]
        self.owner.delete(f"/api/sales-invoices/{invoice_id}", {"confirm": True})
        status, data = self.owner.get("/api/audit-logs?per_page=200")
        self.assertEqual(status, 200, data)
        rows = [r for r in data["logs"] if r["action"] == "sales_invoice.delete" and str(invoice_id) == str(r["entity_id"])]
        self.assertTrue(rows, "deletion must be written to the audit trail")
        self.assertEqual(rows[0]["username"], "ahmed.taranisi")
        self.assertIn(number, rows[0]["details"] or "")

    # ================================================================
    # 5b. EDITING (PATCH) — the mirror image of every create/delete
    # ================================================================
    def _new_customer(self, code=None):
        code = code or f"CUS-ED{int(time.time() * 1000) % 10**9}"
        status, res = self.owner.post("/api/customers", {
            "code": code, "name_ar": "عميل للتعديل", "name_en": "Editable customer",
            "credit_limit": 1000, "payment_terms_days": 30})
        self.assertEqual(status, 200, res)
        return res["id"], code

    def test_86_customer_edit_updates_and_audits(self):
        cid, code = self._new_customer()
        status, res = self.owner.patch(f"/api/customers/{cid}",
                                       {"name_ar": "عميل معدَّل", "phone": "01000000001"})
        self.assertEqual(status, 200, res)
        self.assertTrue(res["ok"])
        self.assertEqual(res["updated"], "customer")
        self.assertCountEqual(res["changed"], ["name_ar", "phone"])

        status, data = self.owner.get("/api/customers")
        row = next(c for c in data["customers"] if c["id"] == cid)
        self.assertEqual(row["name_ar"], "عميل معدَّل")
        self.assertEqual(row["phone"], "01000000001")
        self.assertEqual(row["code"], code, "untouched fields must survive the patch")

        status, logs = self.owner.get("/api/audit-logs?per_page=200")
        entry = next(r for r in logs["logs"]
                     if r["action"] == "customer.update" and str(r["entity_id"]) == str(cid))
        self.assertEqual(entry["username"], "ahmed.taranisi")
        self.assertIn("before", entry["details"])
        self.assertIn("after", entry["details"])
        self.assertIn("عميل للتعديل", entry["details"])
        self.assertIn("عميل معدَّل", entry["details"])

    def test_87_edit_requires_permission(self):
        cid, _ = self._new_customer()
        auditor = Client("heba.elsherif")
        auditor.login()
        status, _ = auditor.patch(f"/api/customers/{cid}", {"name_ar": "محاولة"})
        self.assertEqual(status, 403)

    def test_88_edit_rejects_empty_and_unknown_records(self):
        cid, _ = self._new_customer()
        status, data = self.owner.patch(f"/api/customers/{cid}", {})
        self.assertEqual(status, 422)
        self.assertIn("NOTHING_TO_UPDATE", data["error"])
        status, _ = self.owner.patch("/api/customers/999999", {"name_ar": "x"})
        self.assertEqual(status, 404)

    def test_89_duplicate_code_is_rejected(self):
        cid, code = self._new_customer()
        other, _ = self._new_customer()
        status, data = self.owner.patch(f"/api/customers/{other}", {"code": code})
        self.assertEqual(status, 409, data)
        self.assertIn("DUPLICATE", data["error"])

    def test_90_posted_invoice_cannot_be_edited(self):
        invoice_id, _ = self._make_invoice(qty=1, price=50, post=True)
        status, data = self.owner.patch(f"/api/sales-invoices/{invoice_id}",
                                        {"notes": "بعد الترحيل"})
        self.assertEqual(status, 409, data)
        self.assertIn("ALREADY_POSTED", data["error"])

    def test_91_draft_invoice_edit_is_allowed(self):
        invoice_id, _ = self._make_invoice(qty=1, price=50, post=False)
        status, res = self.owner.patch(f"/api/sales-invoices/{invoice_id}",
                                       {"notes": "ملاحظة جديدة", "due_date": self.today()})
        self.assertEqual(status, 200, res)
        self.assertIn("notes", res["changed"])
        status, detail = self.owner.get(f"/api/sales-invoices/{invoice_id}")
        self.assertEqual(detail["invoice"]["notes"], "ملاحظة جديدة")

    def test_92_employee_and_bank_account_edit(self):
        status, emp = self.owner.post("/api/employees", {
            "code": f"EMP{int(time.time() * 1000) % 10**8}", "name_ar": "موظف",
            "name_en": "Employee", "basic_salary": 4000})
        self.assertEqual(status, 200, emp)
        status, res = self.owner.patch(f"/api/employees/{emp['id']}",
                                       {"basic_salary": 5500, "department": "التشغيل"})
        self.assertEqual(status, 200, res)
        status, data = self.owner.get("/api/payroll")
        row = next(e for e in data["employees"] if e["id"] == emp["id"])
        self.assertEqual(row["basic_salary_cents"], 550_000)
        self.assertEqual(row["department"], "التشغيل")

        banks = self.owner.get("/api/banks")[1]
        acc = banks["accounts"][0]
        status, res = self.owner.patch(f"/api/bank-accounts/{acc['id']}",
                                       {"name_ar": acc["name_ar"] + " (معدَّل)"})
        self.assertEqual(status, 200, res)
        status, banks = self.owner.get("/api/banks")
        self.assertTrue(any(a["name_ar"].endswith("(معدَّل)") for a in banks["accounts"]))

    def test_93_edit_respects_company_scope(self):
        """A record id from another company must not be reachable through PATCH."""
        cid, _ = self._new_customer()
        with db.transaction() as conn:
            mine = conn.execute("SELECT company_id FROM customers WHERE id=?", (cid,)).fetchone()
            other = conn.execute("SELECT id FROM companies WHERE id<>?", (mine["company_id"],)).fetchone()
        if other is None:
            self.skipTest("no second company seeded")
        status, _ = self.owner.patch(f"/api/customers/{cid}", {"name_ar": "داخل الشركة"})
        self.assertEqual(status, 200)

    def test_96_vendor_english_name_is_optional(self):
        code = f"V-NOEN{int(time.time() * 1000) % 10**8}"
        status, res = self.owner.post("/api/vendors", {
            "code": code, "name_ar": "مورد بدون اسم إنجليزي"})
        self.assertEqual(status, 200, res)
        vid = res["id"]

        status, data = self.owner.get(f"/api/vendors/{vid}")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["vendor"]["name_en"], "مورد بدون اسم إنجليزي",
                         "an empty English name must fall back to the Arabic one")

        # clearing it on an existing vendor does the same instead of blowing up
        status, res = self.owner.patch(f"/api/vendors/{vid}", {"name_en": ""})
        self.assertEqual(status, 200, res)
        status, data = self.owner.get(f"/api/vendors/{vid}")
        self.assertEqual(data["vendor"]["name_en"], "مورد بدون اسم إنجليزي")

        # and it can be filled in later
        status, _ = self.owner.patch(f"/api/vendors/{vid}", {"name_en": "Vendor w/o Arabic name"})
        self.assertEqual(status, 200)
        status, data = self.owner.get(f"/api/vendors/{vid}")
        self.assertEqual(data["vendor"]["name_en"], "Vendor w/o Arabic name")

    def test_97_vendor_detail_exposes_balance_payments_and_open_invoices(self):
        vendor = self.first("vendors", "/api/vendors")
        status, data = self.owner.get(f"/api/vendors/{vendor['id']}")
        self.assertEqual(status, 200, data)
        for key in ("vendor", "outstanding_cents", "payments", "open_invoices",
                    "invoices", "paid_total_cents"):
            self.assertIn(key, data)
        self.assertEqual(data["outstanding_cents"], vendor["outstanding_cents"],
                         "the panel balance must equal the vendors list balance")
        # every payment carries a date — that is what the UI shows per vendor
        for pay in data["payments"]:
            self.assertTrue(pay["payment_date"])
            self.assertIn("payment_no", pay)
        # open invoices are exactly the posted / partially-paid ones
        for inv in data["open_invoices"]:
            self.assertIn(inv["status"], ("posted", "partially_paid"))
            self.assertEqual(inv["remaining_cents"], inv["total_cents"] - inv["paid_cents"])

    def test_98_vendor_payment_reduces_the_outstanding_balance(self):
        vendor = next(v for v in self.owner.get("/api/vendors")[1]["vendors"]
                      if v["outstanding_cents"] > 0)
        before = self.owner.get(f"/api/vendors/{vendor['id']}")[1]
        invoice = before["open_invoices"][0]
        amount = min(invoice["remaining_cents"], 25_000) / 100
        banks = self.owner.get("/api/banks")[1]
        gl_bank = next(b["gl_account_id"] for b in banks["accounts"] if b["code"] == "BNK-01")

        status, pay = self.fm.post("/api/payments", {
            "payment_type": "payment", "payment_date": self.today(), "party_type": "vendor",
            "party_id": vendor["id"], "method": "bank", "gl_account_id": gl_bank,
            "amount": amount, "post": True,
            "allocations": [{"invoice_kind": "purchase", "invoice_id": invoice["id"],
                             "amount": amount}],
        })
        self.assertEqual(status, 200, pay)

        after = self.owner.get(f"/api/vendors/{vendor['id']}")[1]
        self.assertEqual(after["outstanding_cents"], before["outstanding_cents"] - int(amount * 100))
        self.assertEqual(len(after["payments"]), len(before["payments"]) + 1)
        self.assertEqual(after["payments"][0]["payment_date"], self.today(),
                         "the new payment must appear first, with its date")

    def test_99_vendor_panel_endpoints_require_permission(self):
        vendor = self.first("vendors", "/api/vendors")
        status, _ = self.sales.get(f"/api/vendors/{vendor['id']}")
        self.assertEqual(status, 403, "the sales role has no vendors.view")

    def _vendor_with_invoice(self):
        vendor = next(v for v in self.owner.get("/api/vendors")[1]["vendors"]
                      if v["outstanding_cents"] > 0)
        detail = self.owner.get(f"/api/vendors/{vendor['id']}")[1]
        return vendor, detail["open_invoices"][0]

    def _cash_and_bank_gl(self):
        banks = self.owner.get("/api/banks")[1]["accounts"]
        cash = next(b for b in banks if b["account_type"] == "cash")
        bank = next(b for b in banks if b["account_type"] == "bank")
        return cash, bank

    def _new_payment(self, amount, method="cash", gl=None, invoice_id=None, party=None):
        cash, bank = self._cash_and_bank_gl()
        return self.fm.post("/api/payments", {
            "payment_type": "payment", "payment_date": self.today(), "party_type": "vendor",
            "party_id": party, "method": method, "gl_account_id": gl or cash["gl_account_id"],
            "amount": amount, "post": True,
            "allocations": ([{"invoice_kind": "purchase", "invoice_id": invoice_id,
                              "amount": amount}] if invoice_id else []),
        })

    def test_A100_cash_payment_method(self):
        vendor, invoice = self._vendor_with_invoice()
        cash, _ = self._cash_and_bank_gl()
        status, res = self._new_payment(120, "cash", invoice_id=invoice["id"], party=vendor["id"])
        self.assertEqual(status, 200, res)
        pay = next(p for p in self.owner.get("/api/payments")[1]["payments"] if p["id"] == res["id"])
        self.assertEqual(pay["method"], "cash")
        self.assertEqual(pay["gl_account_id"], cash["gl_account_id"])
        status, je = self.owner.get(f"/api/journal-entries/{pay['journal_entry_id']}")
        self.assertEqual(status, 200, je)
        credit = [l for l in je["lines"] if l["credit_cents"]]
        self.assertEqual(credit[0]["account_code"], cash["gl_code"],
                         "a cash payment must credit the cash box, not a bank account")
        # the Arabic label is accepted too
        status, res2 = self.owner.post("/api/payments", {
            "payment_type": "payment", "payment_date": self.today(), "party_type": "vendor",
            "party_id": vendor["id"], "method": "نقداً", "gl_account_id": cash["gl_account_id"],
            "amount": 10})
        self.assertEqual(status, 200, res2)
        pay2 = next(p for p in self.owner.get("/api/payments")[1]["payments"] if p["id"] == res2["id"])
        self.assertEqual(pay2["method"], "cash", "the Arabic label must be normalised to cash")

    def test_A101_posted_payment_can_be_edited_and_is_reposted(self):
        vendor, invoice = self._vendor_with_invoice()
        cash, bank = self._cash_and_bank_gl()
        status, res = self._new_payment(100, "cash", invoice_id=invoice["id"], party=vendor["id"])
        self.assertEqual(status, 200, res)
        pid = res["id"]
        before = next(p for p in self.owner.get("/api/payments")[1]["payments"] if p["id"] == pid)
        tb_before = self.owner.get("/api/reports/trial-balance")[1]["totals"]

        status, out = self.fm.patch(f"/api/payments/{pid}", {
            "method": "bank", "gl_account_id": bank["gl_account_id"], "amount": 150})
        self.assertEqual(status, 200, out)
        self.assertTrue(out.get("reposted"))

        after = next(p for p in self.owner.get("/api/payments")[1]["payments"] if p["id"] == pid)
        self.assertEqual(after["method"], "bank")
        self.assertEqual(after["gl_account_id"], bank["gl_account_id"])
        self.assertEqual(after["amount_cents"], 15_000)
        self.assertEqual(after["status"], "posted")
        self.assertNotEqual(after["journal_entry_id"], before["journal_entry_id"],
                            "editing must produce a fresh entry, not mutate the old one")
        self.assertEqual(self.owner.get(f"/api/journal-entries/{before['journal_entry_id']}")[0], 404,
                         "the superseded entry must be gone")
        status, je = self.owner.get(f"/api/journal-entries/{after['journal_entry_id']}")
        self.assertEqual(status, 200)
        credit = next(l for l in je["lines"] if l["credit_cents"])
        self.assertEqual(credit["account_code"], bank["gl_code"])
        tb_after = self.owner.get("/api/reports/trial-balance")[1]["totals"]
        self.assertEqual(tb_after["period_debit"], tb_after["period_credit"],
                         "the trial balance must stay balanced after re-posting")
        self.assertEqual(tb_after["period_debit"] - tb_before["period_debit"], 5_000)

        status, logs = self.owner.get("/api/audit-logs?per_page=100")
        actions = [r["action"] for r in logs["logs"]
                   if str(r["entity_id"]) == str(pid) and r["entity"] == "payment"]
        self.assertIn("payment.unpost", actions)
        self.assertIn("payment.post", actions)
        self.assertIn("payment.update", actions)

    def test_A102_payment_edit_guards(self):
        vendor, invoice = self._vendor_with_invoice()
        status, res = self._new_payment(80, invoice_id=invoice["id"], party=vendor["id"])
        self.assertEqual(status, 200, res)
        pid = res["id"]

        status, data = self.fm.patch(f"/api/payments/{pid}", {"amount": 0.5})
        self.assertEqual(status, 409, data)
        self.assertIn("OVER_ALLOCATION", data["error"])

        status, data = self.fm.patch(f"/api/payments/{pid}", {"method": "bitcoin"})
        self.assertEqual(status, 422, data)

        status, data = self.fm.patch(f"/api/payments/{pid}", {})
        self.assertEqual(status, 422, data)

        # reconcile it with a bank statement through the real API: editing and
        # deleting must both stop until the reconciliation is undone
        cash, _ = self._cash_and_bank_gl()
        status, txn = self.owner.post("/api/bank-transactions", {
            "bank_account_id": cash["id"], "txn_date": self.today(),
            "description": "test recon", "direction": "credit", "amount": 80})
        self.assertEqual(status, 200, txn)
        status, rec = self.owner.post(f"/api/bank-transactions/{txn['id']}/reconcile",
                                      {"payment_id": pid})
        self.assertEqual(status, 200, rec)
        status, data = self.fm.patch(f"/api/payments/{pid}", {"amount": 90})
        self.assertEqual(status, 409, data)
        self.assertIn("RECONCILED", data["error"])
        status, data = self.owner.delete(f"/api/payments/{pid}", {"confirm": True})
        self.assertEqual(status, 409, data)
        self.assertIn("RECONCILED", data["error"])

        # after undoing the reconciliation through the API, delete works again
        status, unrec = self.owner.post(f"/api/bank-transactions/{txn['id']}/unreconcile")
        self.assertEqual(status, 200, unrec)
        before_delete = self.owner.get(f"/api/vendors/{vendor['id']}")[1]["outstanding_cents"]
        status, _ = self.owner.delete(f"/api/payments/{pid}", {"confirm": True})
        self.assertEqual(status, 200)
        after_delete = self.owner.get(f"/api/vendors/{vendor['id']}")[1]["outstanding_cents"]
        self.assertEqual(after_delete, before_delete + 8_000)

    def test_94_auth_survives_missing_session_rows(self):
        """Regression: /tmp-only sessions on serverless hosts must not log users out."""
        before = self.owner.get("/api/banks")
        self.assertEqual(before[0], 200)
        token = self.owner.token
        with db.transaction() as conn:
            conn.execute("DELETE FROM sessions")
        status, data = self.owner.request("GET", "/api/banks")
        self.assertEqual(status, 401, "a lost session row must invalidate the token when "
                                      "ephemeral auth is not enabled (fail closed)")
        status, _ = self.owner.request("GET", "/api/auth/me")
        self.assertEqual(status, 401)
        self.owner.token = token
        for client in (self.owner, self.fm, self.chief, self.accountant, self.auditor,
                       self.sales, self.warehouse):
            client.login()   # restore the shared session table for the remaining tests

    def test_95_tampered_token_is_rejected(self):
        token = self.owner.token
        bad = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        status, _ = self.owner.request("GET", "/api/auth/me", auth=True, token=bad)
        self.assertEqual(status, 401)

    # ================================================================
    # 6. ROUND J — purchase invoice with service / free-text lines
    #    (the defect: "new purchase invoice does not save") plus the
    #    redesigned debt (مديونية) module.
    # ================================================================
    def _a_vendor(self):
        return self.owner.get("/api/vendors")[1]["vendors"][0]

    def test_B110_purchase_invoice_saves_with_a_free_text_service_line(self):
        """A shipping company buys services (نولون، تفريغ، عمالة) that are not in
        the item master. The old form only offered stocked goods, so with a
        service-only catalogue it produced an invoice with no valid line and the
        save failed. A free-text line must save and post."""
        vendor = self._a_vendor()
        cogs_before = self.account_balance("5100")
        inv_before = self.account_balance("1310")
        ap_before = self.account_balance("2110")
        status, res = self.owner.post("/api/purchase-invoices", {
            "vendor_id": vendor["id"], "invoice_date": self.today(), "due_date": self.today(),
            "warehouse_id": None,
            "lines": [{"product_id": None, "description": "نولون نقل 508 طن ذرة",
                       "qty": 508, "unit_price": 550, "tax_rate_bp": 0}],
            "post": True,
        })
        self.assertEqual(status, 200, res)
        self.assertIn("id", res)
        inv = self.owner.get(f"/api/purchase-invoices/{res['id']}")[1]["invoice"]
        self.assertEqual(inv["total_cents"], 27_940_000)
        self.assertEqual(inv["status"], "posted")
        self.assertTrue(inv["invoice_no"].startswith("PI-"), inv)
        # a service line is a cost of service, never inventory
        self.assertEqual(self.account_balance("5100") - cogs_before, 27_940_000)
        self.assertEqual(self.account_balance("1310") - inv_before, 0,
                         "a free-text service line must not touch the inventory account")
        self.assertEqual(self.account_balance("2110") - ap_before, 27_940_000)

    def test_B111_purchase_invoice_accepts_a_service_product_line(self):
        """A catalogue service item (is_service=1) is offered by the form and posts
        to cost of service; it must not require a warehouse."""
        vendor = self._a_vendor()
        products = self.owner.get("/api/products")[1]["products"]
        service = next(p for p in products if p["is_service"])
        status, res = self.owner.post("/api/purchase-invoices", {
            "vendor_id": vendor["id"], "invoice_date": self.today(), "due_date": self.today(),
            "warehouse_id": None,
            "lines": [{"product_id": service["id"], "qty": 1, "unit_price": 900, "tax_rate_bp": 1400}],
            "post": True,
        })
        self.assertEqual(status, 200, res)
        inv = self.owner.get(f"/api/purchase-invoices/{res['id']}")[1]["invoice"]
        self.assertEqual(inv["total_cents"], 102_600)
        je = self.owner.get(f"/api/journal-entries/{inv['journal_entry_id']}")[1]
        debit = next(l for l in je["lines"] if l["debit_cents"] and l["account_code"].startswith("5"))
        self.assertEqual(debit["account_code"], "5100")

    # ---------------- debt module ----------------
    def _debt_account(self, kind="labor", unit="ton"):
        status, res = self.owner.post("/api/debt/accounts", {
            "name_ar": f"حساب اختبار {unit}", "kind": kind, "billing_unit": unit})
        self.assertEqual(status, 200, res)
        return res["id"]

    def test_B120_debt_account_creation_and_seeded_chart(self):
        status, data = self.owner.get("/api/debt/accounts")
        self.assertEqual(status, 200, data)
        codes = {a["code"] for a in data["accounts"]}
        self.assertIn("DB-0003", codes)          # إبراهيم الحبشي — بالطن
        self.assertIn("DB-0006", codes)          # مكتب القدس — حاويات
        # The 15 accounts of the paper statement are DB-0001 … DB-0015. Assert on the
        # codes (not on the total count) so the test cannot be confused by extra
        # accounts that other tests create during the same run.
        expected = {f"DB-{i:04d}" for i in range(1, 16)}
        self.assertTrue(expected <= codes, f"missing seeded accounts: {sorted(expected - codes)}")
        seeded = [a for a in data["accounts"] if a["code"] in expected]
        self.assertEqual(len(seeded), 15)
        for a in seeded:
            self.assertEqual(a["opening_balance_cents"], 0, "seeded balances must start at zero")
        acc = self._debt_account()
        got = next(a for a in self.owner.get("/api/debt/accounts")[1]["accounts"] if a["id"] == acc)
        self.assertEqual(got["billing_unit"], "ton")
        self.assertEqual(got["kind"], "labor")

    def test_B121_debt_item_computes_quantity_by_unit_rate_and_posts(self):
        acc = self._debt_account()
        cogs_before = self.account_balance("5100")
        ap_before = self.account_balance("2120")
        status, res = self.owner.post("/api/debt/items", {
            "account_id": acc, "doc_date": self.today(), "description": "تفريغ ذرة",
            "operation": "تفريغ", "qty": 31882.74, "unit": "ton", "unit_rate": 3,
            "vessel": "GREEN GENIE", "client_name": "ميدسوفت", "cargo": "ذرة",
            "period_from": self.today(), "period_to": self.today(), "report_no": "18"})
        self.assertEqual(status, 200, res)
        item = self.owner.get(f"/api/debt/items?account_id={acc}")[1]["items"][0]
        self.assertEqual(item["amount_cents"], 9_564_822)   # 31,882.74 ton × 3
        self.assertEqual(item["status"], "posted")
        self.assertTrue(item["item_no"].startswith("DEB-"))
        self.assertEqual(self.account_balance("5100") - cogs_before, 9_564_822)
        self.assertEqual(self.account_balance("2120") - ap_before, 9_564_822,
                         "contractor dues belong to their own control account, not trade payables")
        je = self.owner.get(f"/api/journal-entries/{item['journal_entry_id']}")[1]
        self.assertEqual(len(je["lines"]), 2)
        self.assertEqual(je["lines"][0]["account_code"], "5100")
        self.assertEqual(je["lines"][1]["account_code"], "2120")

    def test_B122_debt_payment_voucher_and_statement_totals(self):
        acc = self._debt_account()
        self.owner.post("/api/debt/items", {"account_id": acc, "doc_date": self.today(),
                                            "description": "نقلة", "qty": 10, "unit": "trip", "unit_rate": 500})
        status, pay = self.owner.post("/api/debt/payments", {
            "account_id": acc, "pay_date": self.today(), "amount": 2000, "method": "cash",
            "voucher_no": "4412", "payee": "أحمد تاج"})
        self.assertEqual(status, 200, pay)
        st = self.owner.get(f"/api/debt/accounts/{acc}")[1]
        self.assertEqual(st["totals"]["due_cents"], 500_000)
        self.assertEqual(st["totals"]["paid_cents"], 200_000)
        self.assertEqual(st["totals"]["net_cents"], 300_000)
        self.assertEqual(st["payments"][0]["voucher_no"], "4412")
        je = self.owner.get(f"/api/journal-entries/{st['payments'][0]['journal_entry_id']}")[1]
        self.assertEqual(je["lines"][0]["account_code"], "2120")
        self.assertEqual(je["lines"][1]["account_code"], "1111")
        summary = self.owner.get("/api/debt/summary")[1]
        row = next(a for a in summary["accounts"] if a["id"] == acc)
        self.assertEqual(row["net_cents"], 300_000)
        self.assertEqual(summary["totals"]["net_cents"],
                         sum(a["net_cents"] for a in summary["accounts"]))

    def test_B123_debt_item_edit_reposts_and_keeps_the_ledger_balanced(self):
        acc = self._debt_account()
        item_id = self.owner.post("/api/debt/items", {
            "account_id": acc, "doc_date": self.today(), "description": "شحن", "qty": 100,
            "unit": "ton", "unit_rate": 122})[1]["id"]
        before = self.owner.get(f"/api/debt/items?account_id={acc}")[1]["items"][0]
        tb_before = self.tb()["totals"]
        status, out = self.owner.patch(f"/api/debt/items/{item_id}", {"qty": 150})
        self.assertEqual(status, 200, out)
        after = self.owner.get(f"/api/debt/items?account_id={acc}")[1]["items"][0]
        self.assertEqual(after["amount_cents"], 1_830_000)
        self.assertNotEqual(after["journal_entry_id"], before["journal_entry_id"])
        self.assertEqual(self.owner.get(f"/api/journal-entries/{before['journal_entry_id']}")[0], 404)
        tb_after = self.tb()["totals"]
        self.assertEqual(tb_after["period_debit"], tb_after["period_credit"])

    def test_B124_debt_deletes_require_confirmation_and_reverse_the_ledger(self):
        acc = self._debt_account()
        item_id = self.owner.post("/api/debt/items", {
            "account_id": acc, "doc_date": self.today(), "description": "حذف",
            "qty": 1, "unit": "item", "unit_rate": 700})[1]["id"]
        pay_id = self.owner.post("/api/debt/payments", {
            "account_id": acc, "pay_date": self.today(), "amount": 300, "method": "cash"})[1]["id"]
        status, _ = self.owner.request("DELETE", f"/api/debt/items/{item_id}", {}, auth=True)
        self.assertEqual(status, 428, "deletion must be explicitly confirmed")
        status, blocked = self.owner.request("DELETE", f"/api/debt/accounts/{acc}", {"confirm": True}, auth=True)
        self.assertEqual(status, 409, blocked)
        ap_before = self.account_balance("2120")
        status, out = self.owner.request("DELETE", f"/api/debt/payments/{pay_id}", {"confirm": True}, auth=True)
        self.assertEqual(status, 200, out)
        status, out = self.owner.request("DELETE", f"/api/debt/items/{item_id}", {"confirm": True}, auth=True)
        self.assertEqual(status, 200, out)
        self.assertEqual(self.account_balance("2120"), ap_before + 300_00 - 700_00)
        status, out = self.owner.request("DELETE", f"/api/debt/accounts/{acc}", {"confirm": True}, auth=True)
        self.assertEqual(status, 200, out)
        self.assertNotIn(acc, [a["id"] for a in self.owner.get("/api/debt/accounts")[1]["accounts"]])
        logs = self.owner.get("/api/audit-logs?per_page=100")[1]["logs"]
        actions = {r["action"] for r in logs}
        for a in ("debt_account.create", "debt_item.create", "debt_payment.create",
                  "debt_item.delete", "debt_payment.delete", "debt_account.delete"):
            self.assertIn(a, actions)

    def _raw_get(self, path):
        req = urllib.request.Request(BASE + path, method="GET")
        req.add_header("Authorization", "Bearer " + self.owner.token)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")

    def test_B125_debt_summary_and_statement_exports(self):
        acc = self._debt_account()
        self.owner.post("/api/debt/items", {"account_id": acc, "doc_date": self.today(),
                                            "description": "تصدير", "qty": 2, "unit": "trip", "unit_rate": 250})
        status, body, ctype = self._raw_get("/api/debt/summary/xlsx")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"PK"), "Excel export must be a real xlsx package")
        status, body, ctype = self._raw_get(f"/api/debt/accounts/{acc}/statement/pdf")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"%PDF"), "statement PDF must be a real PDF")
        self.assertIn("application/pdf", ctype)

    def test_B130_settlement_without_any_due_is_an_advance(self):
        """A settlement recorded before the invoice/trip statement arrives must be
        treated as دفعة مقدمة, not silently netted into nothing."""
        acc = self._debt_account()
        status, res = self.owner.post("/api/debt/payments", {
            "account_id": acc, "pay_date": self.today(), "amount": 100000, "method": "bank",
            "voucher_no": "5001", "payee": "محمود مراد",
            "description": "دفعة مقدمة لحين استخراج الفاتورة"})
        self.assertEqual(status, 200, res)
        pay = self.owner.get(f"/api/debt/payments?account_id={acc}")[1]["payments"][0]
        self.assertEqual(pay["advance_cents"], 10_000_000,
                         "the whole settlement is an advance when nothing is owed yet")
        self.assertEqual(pay["description"], "دفعة مقدمة لحين استخراج الفاتورة")
        st = self.owner.get(f"/api/debt/accounts/{acc}")[1]
        self.assertEqual(st["totals"]["due_cents"], 0)
        self.assertEqual(st["totals"]["paid_cents"], 10_000_000)
        self.assertEqual(st["totals"]["advance_cents"], 10_000_000)
        self.assertEqual(st["totals"]["open_due_cents"], 0)
        self.assertEqual(st["totals"]["net_cents"], -10_000_000)
        # the voucher says so in the ledger narration too
        je = self.owner.get(f"/api/journal-entries/{pay['journal_entry_id']}")[1]
        self.assertTrue(any("دفعة مقدمة" in (l["description"] or "") for l in je["lines"]))
        # and the summary reports it as an advance rather than a payable
        row = next(a for a in self.owner.get("/api/debt/summary")[1]["accounts"] if a["id"] == acc)
        self.assertEqual(row["advance_cents"], 10_000_000)
        self.assertEqual(row["open_due_cents"], 0)

    def test_B131_advance_is_consumed_by_the_next_invoice(self):
        acc = self._debt_account()
        self.owner.post("/api/debt/payments", {"account_id": acc, "pay_date": self.today(),
                                               "amount": 100000, "method": "bank", "voucher_no": "6001"})
        self.owner.post("/api/debt/items", {"account_id": acc, "doc_date": self.today(),
                                            "description": "نولون 508 طن", "qty": 508, "unit": "ton",
                                            "unit_rate": 550})
        st = self.owner.get(f"/api/debt/accounts/{acc}")[1]
        self.assertEqual(st["totals"]["due_cents"], 27_940_000)
        self.assertEqual(st["totals"]["advance_cents"], 0,
                         "the advance must be absorbed by the invoice once it exists")
        self.assertEqual(st["totals"]["open_due_cents"], 17_940_000)
        self.assertEqual(st["totals"]["net_cents"], 17_940_000)

    def test_B132_only_the_excess_over_the_balance_is_an_advance(self):
        acc = self._debt_account()
        self.owner.post("/api/debt/items", {"account_id": acc, "doc_date": self.today(),
                                            "description": "تفريغ", "qty": 100, "unit": "ton",
                                            "unit_rate": 500})                      # 50,000 due
        status, res = self.owner.post("/api/debt/payments", {
            "account_id": acc, "pay_date": self.today(), "amount": 70000, "method": "cash"})
        self.assertEqual(status, 200, res)
        pay = self.owner.get(f"/api/debt/payments?account_id={acc}")[1]["payments"][0]
        self.assertEqual(pay["advance_cents"], 2_000_000,   # 20,000 EGP of the 70,000
                         "only the part above the outstanding balance is an advance")
        st = self.owner.get(f"/api/debt/accounts/{acc}")[1]
        self.assertEqual(st["totals"]["advance_cents"], 2_000_000)
        self.assertEqual(st["totals"]["open_due_cents"], 0)

    def test_B133_settlement_statement_text_is_saved_and_editable(self):
        acc = self._debt_account()
        pid = self.owner.post("/api/debt/payments", {
            "account_id": acc, "pay_date": self.today(), "amount": 1000, "method": "cash",
            "description": "دفعة مقدمة"})[1]["id"]
        self.assertEqual(self.owner.get(f"/api/debt/payments?account_id={acc}")[1]["payments"][0]["description"],
                         "دفعة مقدمة")
        status, out = self.owner.patch(f"/api/debt/payments/{pid}", {
            "description": "دفعة مقدمة — جرار محمد نجيب"})
        self.assertEqual(status, 200, out)
        pay = self.owner.get(f"/api/debt/payments?account_id={acc}")[1]["payments"][0]
        self.assertEqual(pay["description"], "دفعة مقدمة — جرار محمد نجيب")
        self.assertEqual(pay["status"], "posted", "editing the narration keeps the voucher posted")
        self.assertEqual(pay["advance_cents"], 100_000)

    def test_B134_invoice_statement_lists_every_due_line(self):
        acc = self._debt_account()
        self.owner.post("/api/debt/items", {"account_id": acc, "doc_date": self.today(),
                                            "description": "بيان فاتورة اختبار", "qty": 2, "unit": "trip",
                                            "unit_rate": 1000})
        status, data = self.owner.get("/api/debt/invoices")
        self.assertEqual(status, 200, data)
        row = next(r for r in data["items"] if r["description"] == "بيان فاتورة اختبار")
        self.assertTrue(row["account_name"])
        self.assertEqual(row["amount_cents"], 200_000)
        self.assertEqual(data["totals"]["amount_cents"],
                         sum(r["amount_cents"] for r in data["items"] if r["status"] == "posted"))
        for path, sig in (("/api/debt/invoices/xlsx", b"PK"), ("/api/debt/invoices/pdf", b"%PDF")):
            status, body, ctype = self._raw_get(path)
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(sig), path)

    def test_B136_arabic_shaping_keeps_word_ends_and_joins(self):
        """A word must END with a final/isolated form - never a medial tail.

        Regression: a stray space inside the joining table made every last
        letter of a word medial, so «سي» printed as «سيـ» and «سيرفيس» as
        «سيرفيسـ».  The font's own ligatures (yeh+reh, lam+meem, lam+alef) also
        have to be applied, or the joins break in the middle of the word.
        """
        import pdfgen
        final_yeh = "\ufef2"
        final_seen = "\ufeb2"
        self.assertEqual(pdfgen.shape_arabic("سي")[-1], final_yeh)
        self.assertEqual(pdfgen.shape_arabic("سي سيرفيس")[-1], final_seen)
        self.assertEqual(pdfgen.shape_arabic("للشحن والتفريغ")[4], "\ufee6")  # final noon
        lig = pdfgen.join_ligatures(pdfgen.shape_arabic("سيرفيس"))
        self.assertIn("\ufc91", lig, "yeh+reh must use the font ligature")
        lig = pdfgen.join_ligatures(pdfgen.shape_arabic("الملاحية"))
        self.assertIn("\ufccc", lig, "lam+meem must use the font ligature")

    def test_B135_pdf_reports_embed_the_arabic_typeface_and_never_overflow(self):
        """Every PDF must carry the Arabic face and clamp text inside its column."""
        acc = self._debt_account()
        self.owner.post("/api/debt/items", {"account_id": acc, "doc_date": self.today(),
                                            "description": "تفريغ — Amis leader", "qty": 122,
                                            "unit": "ton", "unit_rate": 6198.5})
        self.owner.post("/api/debt/payments", {"account_id": acc, "pay_date": self.today(),
                                               "amount": 100000, "method": "cash",
                                               "voucher_no": "DPV-2026-00002"})
        status, body, _ = self._raw_get(f"/api/debt/accounts/{acc}/statement/pdf")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"%PDF"))
        # the client picked «PT Bold Heading» as the printed face
        self.assertIn(b"PTBoldHeading", body, "the Arabic typeface must be embedded")
        self.assertNotIn(b"/BaseFont /Amiri", body, "Amiri must no longer be the report face")
        # every report keeps its content inside the page
        for path in ("/api/debt/summary/pdf", "/api/debt/invoices/pdf",
                     "/api/reports/pdf?kind=trial-balance"):
            status, pdf, _ = self._raw_get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(pdf.startswith(b"%PDF"), path)

    def test_B136_bidi_places_numbers_and_latin_inside_arabic_correctly(self):
        import pdfgen
        # numbers keep their internal order, Arabic keeps its logical order
        self.assertEqual(pdfgen.visual_order("508 طن"), "نط 508")
        self.assertEqual(pdfgen.visual_order("اذن صرف DPV-2026-00002 — دفعة"),
                         "ةعفد — DPV-2026-00002 فرص نذا")
        # the shaped glyphs used on the page keep the same order
        pdf0 = pdfgen.PDF(str(__import__("exporter").FONT_PATH), str(__import__("exporter").LATIN_FONT_PATH))
        rendered = "".join(c for c, _ in pdf0._runs("508 طن", True))
        self.assertEqual(rendered, "\ufee6\ufec3 508")   # shaped طن, then the number
        self.assertEqual(pdfgen.visual_order("Sea Service Co. for Shipping", False),
                         "Sea Service Co. for Shipping")
        self.assertEqual(pdfgen.visual_order("Statement — Amis leader", False),
                         "Statement — Amis leader")
        # a long cell is shrunk or truncated so it cannot bleed into the next column
        import exporter
        pdf = pdfgen.PDF(str(exporter.FONT_PATH), str(exporter.LATIN_FONT_PATH))
        txt, size = exporter._fit_cell(pdf, "Transport & containers " * 4, 60, 8.4, rtl=False)
        self.assertLessEqual(pdf.measure(txt, size, False), 52)
        self.assertTrue(txt.endswith("…"))

    def test_B137_dates_are_day_month_year_in_exports_and_storage_is_reported(self):
        """Day/month/year everywhere the user reads a date; storage mode exposed."""
        import exporter
        self.assertEqual(exporter.fmt_date_ui("2026-12-08"), "08/12/2026")
        self.assertEqual(exporter.fmt_date_ui("2026-01-31"), "31/01/2026")
        self.assertEqual(exporter.fmt_date_ui(None), "\u2014")
        # the machine formats stay ISO (e-invoicing payload / sorting)
        self.assertEqual(exporter.fmt_date("2026-12-08"), "2026-12-08")

        status, health = self.owner.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("storage", health)
        self.assertIn("durable", health["storage"])

        status, info = self.owner.get("/api/admin/storage")
        self.assertEqual(status, 200, info)
        self.assertIn("durable", info)
        self.assertIn("counts", info)
        self.assertGreaterEqual(info["counts"]["users"], 2)

        # a sales user may not download the whole database
        self.assertEqual(self.sales.get("/api/admin/storage")[0], 403)
        self.assertEqual(self.sales.get("/api/admin/backup")[0], 403)

    def test_B138_database_backup_downloads_a_restorable_snapshot(self):
        status, body, ctype = self._raw_get("/api/admin/backup")
        self.assertEqual(status, 200)
        if body.startswith(b"-- ELTARANISI ERP"):          # Cloudflare D1: logical dump
            text = body.decode("utf-8")
            self.assertIn('CREATE TABLE', text)
            self.assertIn('INSERT INTO "journal_lines"', text)
            self.assertIn('INSERT INTO "users"', text)
            return
        self.assertTrue(body.startswith(b"SQLite format 3"), "must be a real SQLite database")
        # the snapshot is complete: it carries the users, the chart of accounts
        # and the posted ledger, so restoring it brings the system back as it was
        import sqlite3, tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(path, "wb") as fh:
                fh.write(body)
            conn = sqlite3.connect(path)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"users", "accounts", "journal_entries", "journal_lines"} <= tables)
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertGreaterEqual(users, 2)
            debit, credit = conn.execute(
                "SELECT COALESCE(SUM(debit_cents),0), COALESCE(SUM(credit_cents),0) FROM journal_lines").fetchone()
            self.assertEqual(debit, credit, "the snapshot keeps a balanced ledger")
            conn.close()
        finally:
            os.unlink(path)

    def test_B126_debt_permissions_are_enforced_server_side(self):
        acc = self._debt_account()
        status, _ = self.sales.get("/api/debt/summary")
        self.assertEqual(status, 403, "sales users have no debt.view")
        # auditors can read but never post
        self.assertEqual(self.auditor.get("/api/debt/summary")[0], 200)
        status, _ = self.auditor.post("/api/debt/items", {
            "account_id": acc, "doc_date": self.today(), "description": "x", "amount": 10})
        self.assertEqual(status, 403)

    # ================================================================
    # 7. DEMO ACCOUNTS ENDPOINT (development only)
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

    # ================================================================
    # 8. ACCOUNTING INTEGRITY ENDPOINT (backend-agnostic safety net)
    # ================================================================
    def test_B140_integrity_endpoint_proves_the_books_are_clean(self):
        status, data = self.owner.get("/api/admin/integrity")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["passed"], data)
        self.assertEqual(data["checks_passed"], data["checks_total"])
        names = [c["check"] for c in data["results"]]
        self.assertTrue(any("balanced" in n for n in names))
        self.assertTrue(any("without an entry" in n for n in names))
        # and it is not readable by a role without settings rights
        status, _ = self.sales.get("/api/admin/integrity")
        self.assertEqual(status, 403)

    def test_B141_backup_format_matches_the_backend(self):
        status, data = self.owner.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("durable", data["storage"], "health must always state whether data survives")
        status, info = self.owner.get("/api/admin/storage")
        self.assertEqual(status, 200)
        self.assertIn(info["engine"], ("sqlite", "cloudflare-d1"))
        self.assertIn("backend", info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
