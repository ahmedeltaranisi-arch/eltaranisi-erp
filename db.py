"""Database access layer for ELTARANISI ERP (SQLite, stdlib only)."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ERP_DB", BASE_DIR / "data" / "erp.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def transaction():
    """Atomic transaction: everything commits or nothing does."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def read():
    return connect()


def init_db(force: bool = False) -> None:
    if force and DB_PATH.exists():
        DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink()
    conn = connect()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.close()


# Additive column migrations: (table, column, SQL type/definition).
# `schema.sql` is re-applied to existing databases, but CREATE TABLE IF NOT EXISTS
# never adds a column to a table that already exists — these do.
COLUMN_MIGRATIONS = [
    ("debt_payments", "description", "TEXT"),
    ("debt_payments", "advance_cents", "INTEGER NOT NULL DEFAULT 0"),
]


def _ensure_columns(conn) -> None:
    for table, column, ddl in COLUMN_MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if not cols or column in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.Error:
            pass


def migrate() -> None:
    """Apply additive, idempotent schema changes to an existing database.

    Runs `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` statements
    from schema.sql, so an older database picks up new tables without a rebuild.
    """
    if not DB_PATH.exists():
        return
    conn = connect()
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            script = fh.read()
        for stmt in script.split(";"):
            # drop leading SQL comments so the statement keyword is detected
            lines = [ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")]
            stmt = "\n".join(lines)
            head = stmt.strip()[:80].upper()
            if head.startswith("CREATE TABLE IF NOT EXISTS") or head.startswith("CREATE INDEX IF NOT EXISTS") \
                    or head.startswith("CREATE VIEW IF NOT EXISTS"):
                try:
                    conn.execute(stmt)
                except sqlite3.Error:
                    pass
        _ensure_columns(conn)
        _sync_system_roles(conn)
    finally:
        conn.close()


def _sync_system_roles(conn) -> None:
    """Refresh the DB copy of the built-in roles from code.

    Permissions are defined in `security.ROLE_DEFINITIONS`; the table is only a
    projection of them. Without this, a permission added in a new release would
    stay invisible on a database created by an older seed.
    """
    import json
    try:
        from security import ROLE_DEFINITIONS
    except Exception:
        return
    for role in ROLE_DEFINITIONS:
        try:
            conn.execute(
                """UPDATE roles SET name_ar=?, name_en=?, permissions=?, data_scope=?
                   WHERE code=? AND COALESCE(is_system,1)=1""",
                (role["name_ar"], role["name_en"], json.dumps(role["permissions"]),
                 role["data_scope"], role["code"]))
        except sqlite3.Error:
            pass
    conn.commit()


# --------------------------------------------------------------------------
# Money helpers — all amounts are integer minor units (piastres)
# --------------------------------------------------------------------------
def to_cents(value) -> int:
    """Convert a user supplied amount to integer minor units safely."""
    if value is None or value == "":
        return 0
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_AMOUNT")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents) -> float:
    if cents is None:
        return 0.0
    return float(Decimal(int(cents)) / Decimal(100))


def money_str(cents) -> str:
    if cents is None:
        cents = 0
    d = (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:,.2f}"


def to_milli(value) -> int:
    if value is None or value == "":
        return 0
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_QUANTITY")
    return int((d * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()} if row is not None else None


def rows_to_list(rows) -> list:
    return [row_to_dict(r) for r in rows]
