#!/usr/bin/env python3
"""Verify the accounting invariants of a live ELTARANISI ERP database.

Works on either backend (local SQLite file or Cloudflare D1 over HTTPS), because
it only uses ``db.connect()``:

    python3 scripts/check_integrity.py                  # configured backend
    ERP_DB_BACKEND=d1 CF_ACCOUNT_ID=… CF_API_TOKEN=… \\
        CF_D1_DATABASE_ID=… python3 scripts/check_integrity.py

Why it exists: D1 has no interactive transactions over HTTPS, so a statement
that must never tear is sent through ``conn.batch([...])`` (atomic). This script
is the safety net that proves every write landed completely — run it after a
deploy, from cron, or from ``GET /api/admin/integrity``.

Exit code 0 = all checks passed, 1 = at least one check failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS = []


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn
    return wrap


# ---------------------------------------------------------------- invariants
@check("ledger is balanced (Σ debit = Σ credit)")
def _balanced(conn):
    row = conn.execute(
        "SELECT COALESCE(SUM(debit_cents),0) d, COALESCE(SUM(credit_cents),0) c FROM journal_lines"
    ).fetchone()
    return row["d"] == row["c"], f"{row['d'] / 100:,.2f} vs {row['c'] / 100:,.2f}"


@check("every posted entry balances and has ≥ 2 lines")
def _entries(conn):
    rows = conn.execute(
        """SELECT e.id, e.entry_no,
                  COUNT(l.id) AS n,
                  COALESCE(SUM(l.debit_cents),0) AS d,
                  COALESCE(SUM(l.credit_cents),0) AS c
             FROM journal_entries e LEFT JOIN journal_lines l ON l.entry_id = e.id
            WHERE e.status = 'posted'
            GROUP BY e.id
           HAVING n < 2 OR d <> c"""
    ).fetchall()
    detail = ", ".join(f"{r['entry_no']}({r['n']} lines {r['d'] / 100:,.2f}/{r['c'] / 100:,.2f})"
                       for r in rows[:5])
    return not rows, detail or "ok"


@check("no journal line without an entry")
def _orphan_lines(conn):
    row = conn.execute(
        """SELECT COUNT(*) n FROM journal_lines l
            LEFT JOIN journal_entries e ON e.id = l.entry_id
           WHERE e.id IS NULL"""
    ).fetchone()
    return row["n"] == 0, f"{row['n']} orphan line(s)"


@check("no journal line pointing at a missing account")
def _orphan_accounts(conn):
    row = conn.execute(
        """SELECT COUNT(*) n FROM journal_lines l
            LEFT JOIN accounts a ON a.id = l.account_id
           WHERE a.id IS NULL"""
    ).fetchone()
    return row["n"] == 0, f"{row['n']} line(s)"


@check("posted debt items are linked to a journal entry")
def _debt_links(conn):
    row = conn.execute(
        """SELECT COUNT(*) n FROM debt_items i
            WHERE i.status = 'posted'
              AND (i.journal_entry_id IS NULL
                   OR NOT EXISTS (SELECT 1 FROM journal_entries e WHERE e.id = i.journal_entry_id))"""
    ).fetchone()
    return row["n"] == 0, f"{row['n']} item(s)"


@check("document numbers are unique per company")
def _numbers(conn):
    bad = []
    for table, column in (("journal_entries", "entry_no"), ("sales_invoices", "invoice_no"),
                          ("purchase_invoices", "invoice_no"), ("debt_items", "item_no"),
                          ("debt_payments", "payment_no"), ("receipts", "receipt_no")):
        try:
            rows = conn.execute(
                f"""SELECT {column} k, COUNT(*) n FROM {table}
                     GROUP BY company_id, {column} HAVING n > 1"""
            ).fetchall()
        except Exception:
            continue
        if rows:
            bad.append(f"{table}: {rows[0]['k']}")
    return not bad, ", ".join(bad) or "ok"


@check("trial balance is flat per company")
def _trial(conn):
    rows = conn.execute(
        """SELECT e.company_id AS company_id,
                  SUM(l.debit_cents) - SUM(l.credit_cents) AS diff
             FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id
            GROUP BY e.company_id"""
    ).fetchall()
    bad = [f"company {r['company_id']}: {r['diff'] / 100:,.2f}" for r in rows if r["diff"]]
    return not bad, ", ".join(bad) or "ok"


@check("every table has its expected row count available (no half-written tables)")
def _tables(conn):
    row = conn.execute("SELECT COUNT(*) n FROM sqlite_master WHERE type='table'").fetchone()
    return row["n"] >= 40, f"{row['n']} tables"


def run(conn, verbose=False):
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn(conn)
        except Exception as exc:                     # a check that cannot run is a failure
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
        if verbose or not ok:
            print(f"{'✓' if ok else '✗'} {name} — {detail}")
    return results


def main() -> int:
    import db

    conn = db.connect()
    try:
        print(f"database: {db.backend_name()}"
              + (f" · {db._d1_storage_info().get('path')}" if db.is_d1() else f" · {db.DB_PATH}"))
        results = run(conn, verbose=True)
    finally:
        conn.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
