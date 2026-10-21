#!/usr/bin/env python3
"""Restore an ELTARANISI ERP database from a downloaded backup file.

Usage
-----
    python3 scripts/restore_backup.py eltaranisi-erp-backup-20261021-104500.db

    # restore into a different target (e.g. to inspect it first)
    ERP_DB=/tmp/inspect.db python3 scripts/restore_backup.py backup.db

Safety
------
* The current database is never overwritten in place: it is first copied to
  ``erp-replaced-<timestamp>.db`` next to the target, so a wrong restore can be
  undone.
* The backup is verified before anything is replaced: it must be a SQLite file
  with the expected tables and a balanced general ledger.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED_TABLES = {"users", "roles", "companies", "accounts", "journal_entries",
                   "journal_lines", "sales_invoices", "purchase_invoices",
                   "debt_items", "debt_payments"}


def verify(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"✗ file not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise SystemExit(f"✗ not an ELTARANISI backup — missing tables: {', '.join(sorted(missing))}")
        debit, credit = conn.execute(
            "SELECT COALESCE(SUM(debit_cents),0), COALESCE(SUM(credit_cents),0) FROM journal_lines"
        ).fetchone()
        if debit != credit:
            raise SystemExit(f"✗ ledger is not balanced in this backup ({debit} ≠ {credit}) — refusing to restore")
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print(f"✓ verified: {len(tables)} tables · {users} users · ledger balanced ({debit / 100:,.2f})")
    finally:
        conn.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = Path(sys.argv[1]).expanduser().resolve()
    verify(source)

    target = Path(os.environ.get("ERP_DB") or (ROOT / "data" / "erp.db")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        keep = target.with_name(f"erp-replaced-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(target, keep)
        print(f"  current database copied to {keep.name}")
        for suffix in ("-wal", "-shm"):
            side = Path(str(target) + suffix)
            if side.exists():
                side.unlink()

    shutil.copy2(source, target)
    print(f"✓ restored {source.name} → {target}")
    print("  restart the server so every connection picks up the restored file.")


if __name__ == "__main__":
    main()
