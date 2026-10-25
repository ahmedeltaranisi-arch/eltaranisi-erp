"""اختبارات إعاشة العاملين — subsistence allowance.

Two layers:

* **Unit** — the day-count / money maths, which is the part that must never be
  wrong (`python3 -m pytest tests/test_allowance.py -k unit`).
* **Integration** — the REST flow against a live server: create a sheet, save
  the day grid, verify the totals, post it to the ledger and prove the entry is
  balanced and the sheet becomes read-only.

    python3 seed.py
    PORT=8000 python3 server.py &
    python3 -m pytest tests/test_allowance.py -q
"""
from __future__ import annotations

import os
import sys
import unittest
import urllib.error
import urllib.request
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import allowance  # noqa: E402

BASE = os.environ.get("ERP_BASE", "http://127.0.0.1:8000")


def _credentials() -> dict:
    creds = {}
    path = os.path.join(ROOT, "data", "dev-credentials.txt")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 4:
                creds[parts[1]] = parts[2]
    return creds


class AllowanceMathTest(unittest.TestCase):
    """unit — no server, no database."""

    def test_period_days(self):
        self.assertEqual(allowance.period_days("2026-09-01", "2026-09-30"), 30)
        self.assertEqual(allowance.period_days("2026-02-01", "2026-02-28"), 28)
        self.assertEqual(allowance.period_days("2026-09-05", "2026-09-05"), 1)
        with self.assertRaises(allowance.AllowanceError):
            allowance.period_days("2026-09-30", "2026-09-01")

    def test_day_codes_drive_the_counts(self):
        line = allowance.compute_line({
            "daily_rate": "30",
            "day_codes": {"1": "ام", "2": "ام", "3": "غ", "4": "م", "5": "خ", "6": "بم"},
        }, 30)
        self.assertEqual(line["leave_sick"], 2)
        self.assertEqual(line["absence_days"], 1)
        self.assertEqual(line["mission_days"], 1)
        self.assertEqual(line["ded_allowance_days"], 1)
        self.assertEqual(line["leave_unpaid"], 1)
        # 30 − (2 + 1 + 1 + 1) = 25 present, minus 1 allowance deduction = 24 net
        self.assertEqual(line["present_days"], 25)
        self.assertEqual(line["net_days"], 24)
        self.assertEqual(line["total_cents"], 24 * 3000)

    def test_manual_counts_and_adjustments(self):
        line = allowance.compute_line({
            "daily_rate": 35, "leave_annual": 6, "ded_salary_days": 1, "adjustments": "12.50",
        }, 30)
        self.assertEqual(line["present_days"], 24)
        self.assertEqual(line["net_days"], 23)
        self.assertEqual(line["total_cents"], 23 * 3500 + 1250)

    def test_unknown_codes_and_out_of_range_days_are_ignored(self):
        line = allowance.compute_line({"daily_rate": 30, "day_codes": {"1": "xx", "99": "غ", "2": "غ"}}, 30)
        self.assertEqual(line["absence_days"], 1)
        self.assertEqual(json.loads(line["day_codes"]), {"2": "غ"})

    def test_leave_days_cannot_exceed_the_period(self):
        with self.assertRaises(allowance.AllowanceError):
            allowance.compute_line({"daily_rate": 30, "leave_annual": 25, "absence_days": 10}, 30)

    def test_summarize(self):
        lines = [
            allowance.compute_line({"daily_rate": 30, "day_codes": {"1": "غ"}}, 30),
            allowance.compute_line({"daily_rate": 40, "day_codes": {"1": "خ", "2": "خ"}}, 30),
        ]
        stats = allowance.summarize(lines)
        self.assertEqual(stats["people_count"], 2)
        self.assertEqual(stats["total_days"], 29 + 28)
        self.assertEqual(stats["total_cents"], 29 * 3000 + 28 * 4000)


class AllowanceApiTest(unittest.TestCase):
    """integration — needs a running seeded server."""

    token = None
    period_id = None

    @classmethod
    def setUpClass(cls):
        creds = _credentials()
        if "ahmed.taranisi" not in creds:
            raise unittest.SkipTest("dev credentials missing — seed first")
        body = json.dumps({"username": "ahmed.taranisi", "password": creds["ahmed.taranisi"]}).encode()
        req = urllib.request.Request(BASE + "/api/auth/login", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                cls.token = json.loads(resp.read())["token"]
        except (urllib.error.URLError, OSError) as exc:
            raise unittest.SkipTest(f"server not reachable: {exc}")

    def call(self, method, path, payload=None, expect=200):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_01_meta_has_departments_and_people(self):
        status, data = self.call("GET", "/api/allowance/meta")
        self.assertEqual(status, 200)
        self.assertTrue(data["departments"])
        self.assertIn("code_labels", data)

    def test_02_create_save_and_total(self):
        _, data = self.call("GET", "/api/allowance/meta")
        dept = data["departments"][0]
        payload = {"department": dept, "start_date": "2026-02-01", "end_date": "2026-02-28",
                   "copy_roster": True}
        status, created = self.call("POST", "/api/allowance/periods", payload)
        if status == 409:                      # an earlier run left a sheet behind
            _, listing = self.call("GET", "/api/allowance/periods")
            match = [x for x in listing["periods"]
                     if x["department"] == dept and x["start_date"] == payload["start_date"]]
            self.assertTrue(match, "duplicate reported but no sheet found")
            if match[0]["status"] == "posted":
                self.skipTest("sheet already posted by an earlier run")
            self.call("DELETE", f"/api/allowance/periods/{match[0]['id']}")
            status, created = self.call("POST", "/api/allowance/periods", payload)
        self.assertEqual(status, 200, created)
        type(self).period_id = created["id"]
        self.addCleanup(lambda: None)
        # duplicate must be refused
        status, dup = self.call("POST", "/api/allowance/periods", payload)
        self.assertEqual(status, 409)

        status, sheet = self.call("GET", f"/api/allowance/periods/{self.period_id}")
        self.assertEqual(status, 200)
        self.assertEqual(sheet["days"], 28)
        self.assertTrue(sheet["lines"])

        lines = []
        for i, line in enumerate(sheet["lines"]):
            entry = {"id": line["id"], "name_ar": line["name_ar"], "daily_rate": line["daily_rate_cents"] / 100}
            if i == 0:
                entry["day_codes"] = {"5": "ام", "6": "غ"}
            lines.append(entry)
        status, saved = self.call("PUT", f"/api/allowance/periods/{self.period_id}", {"lines": lines})
        self.assertEqual(status, 200, saved)

        _, fresh = self.call("GET", f"/api/allowance/periods/{self.period_id}")
        expected = sum(l["total_cents"] for l in fresh["lines"])
        self.assertEqual(fresh["totals"]["total_cents"], expected)
        first = fresh["lines"][0]
        self.assertEqual(first["leave_sick"], 1)
        self.assertEqual(first["absence_days"], 1)
        expected_net = fresh["days"] - 2          # two days away from the grid
        self.assertEqual(first["present_days"], expected_net)
        self.assertEqual(first["net_days"], expected_net)
        self.assertEqual(first["total_cents"], expected_net * first["daily_rate_cents"])

    def test_03_post_creates_balanced_entry_and_locks(self):
        if not self.period_id:
            self.skipTest("no sheet created")
        status, posted = self.call("POST", f"/api/allowance/periods/{self.period_id}/post", {})
        self.assertEqual(status, 200, posted)
        entry_id = posted["journal_entry_id"]
        _, data = self.call("GET", f"/api/journal-entries/{entry_id}")
        entry, lines = data["entry"], data["lines"]
        debit = sum(l["debit_cents"] for l in lines)
        credit = sum(l["credit_cents"] for l in lines)
        self.assertEqual(debit, credit)
        self.assertEqual(debit, posted["total_cents"])
        self.assertEqual(entry["status"], "posted")
        codes = {l["account_code"] for l in lines}
        self.assertEqual(codes, {"5215", "2140"})
        # posted sheet is read-only and cannot be deleted
        status, err = self.call("PUT", f"/api/allowance/periods/{self.period_id}", {"lines": []})
        self.assertEqual(status, 409)
        status, err = self.call("DELETE", f"/api/allowance/periods/{self.period_id}")
        self.assertEqual(status, 409)

    def test_04_exports(self):
        if not self.period_id:
            self.skipTest("no sheet created")
        for path, ctype in ((f"/api/allowance/periods/{self.period_id}/xlsx", "spreadsheet"),
                            (f"/api/allowance/periods/{self.period_id}/pdf?grid=0", "pdf")):
            req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {self.token}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(ctype, resp.headers.get("Content-Type", ""))
                self.assertGreater(len(resp.read()), 1000)

    def test_05_non_privileged_user_is_denied_manage(self):
        creds = _credentials()
        if "heba.elsherif" not in creds:
            self.skipTest("auditor account missing")
        body = json.dumps({"username": "heba.elsherif", "password": creds["heba.elsherif"]}).encode()
        req = urllib.request.Request(BASE + "/api/auth/login", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            token = json.loads(resp.read())["token"]
        req = urllib.request.Request(BASE + "/api/allowance/periods", data=b"{}", method="POST",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=20)
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
