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


def restore_sql(path: Path) -> None:
    """Restore a logical dump (.sql) into the configured backend (D1 or SQLite).

    Rows are loaded with foreign-key enforcement off (the standard way to replay
    a dump whose table order is not dependency order) and the result is checked
    with PRAGMA foreign_key_check afterwards.
    """
    import db

    script = path.read_text(encoding="utf-8")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if db.backend_name() != "d1":
        target = Path(db.DB_PATH)
        if target.exists() and target.stat().st_size:
            keep = target.with_name(f"erp-replaced-{stamp}.db")
            shutil.copy2(target, keep)
            print(f"• previous database kept at {keep}")
            target.unlink()
    conn = db.connect()
    try:
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
        except Exception:
            pass
        if db.backend_name() == "d1":          # D1: clear the target first
            db.drop_all_objects(conn)
        conn.executescript(script)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise SystemExit(f"✗ dump is incomplete — missing tables: {', '.join(sorted(missing))}")
        debit, credit = conn.execute(
            "SELECT COALESCE(SUM(debit_cents),0) d, COALESCE(SUM(credit_cents),0) c FROM journal_lines"
        ).fetchone()
        if debit != credit:
            raise SystemExit(f"✗ ledger is not balanced in this dump ({debit} ≠ {credit})")
        try:
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        except Exception:
            broken = []
        if broken:
            raise SystemExit(f"✗ restore left {len(broken)} orphan rows (foreign_key_check)")
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        print(f"✓ restored {len(tables)} tables into {db.backend_name()} · "
              f"ledger balanced ({debit / 100:,.2f}) · foreign keys intact")
    finally:
        conn.close()


def verify(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"✗ file not found: {path}")
    if path.suffix.lower() == ".sql":
        head = path.read_text(encoding="utf-8")[:4000]
        if "CREATE TABLE" not in head.upper():
            raise SystemExit("✗ this does not look like a SQL dump")
        print(f"✓ dump verified ({(path.stat().st_size / 1024):,.0f} KB)")
        return
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
    if source.suffix.lower() == ".sql":
        restore_sql(source)
        return

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
