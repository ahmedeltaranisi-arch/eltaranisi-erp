"""Database access layer for ELTARANISI ERP (SQLite, stdlib only)."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ERP_DB", BASE_DIR / "data" / "erp.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"


# Directories that are wiped when the host recycles the instance (Vercel /
# Lambda put the writable filesystem in /tmp). A database living there is
# *temporary*: it is re-seeded from scratch on the next cold start.
EPHEMERAL_ROOTS = ("/tmp", "/var/tmp", "/dev/shm", "/run")


def is_ephemeral() -> bool:
    try:
        path = str(DB_PATH.resolve())
    except Exception:
        path = str(DB_PATH)
    return any(path == root or path.startswith(root + os.sep) for root in EPHEMERAL_ROOTS)


def storage_info() -> dict:
    """Where the data lives and whether it survives a restart."""
    if BACKEND == "d1":
        return _d1_storage_info()
    info = {
        "path": str(DB_PATH),
        "durable": not is_ephemeral(),
        "engine": "sqlite",
        "exists": DB_PATH.exists(),
    }
    try:
        if DB_PATH.exists():
            stat = DB_PATH.stat()
            info["size_bytes"] = stat.st_size
            info["modified_at"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        pass
    try:
        conn = connect()
        info["counts"] = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("journal_entries", "sales_invoices", "purchase_invoices",
                          "debt_items", "debt_payments", "users")
        }
        conn.close()
    except Exception:
        info["counts"] = {}
    return info


def _d1_storage_info() -> dict:
    info = {"path": f"cloudflare-d1:{os.environ.get('CF_D1_DATABASE_ID') or os.environ.get('CF_D1_DATABASE') or '?'}",
            "durable": True, "engine": "cloudflare-d1", "exists": True}
    try:
        conn = connect()
        info["counts"] = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("journal_entries", "sales_invoices", "purchase_invoices",
                          "debt_items", "debt_payments", "users")
        }
        conn.close()
    except Exception:
        info["counts"] = {}
    return info


def snapshot(target_path) -> str:
    """Consistent copy of the live database (safe while the server is running).

    Local SQLite -> binary file copy via the online backup API.
    Cloudflare D1 -> portable SQL dump (schema + rows), the only export shape a
    managed HTTP database can give us synchronously.
    """
    if BACKEND == "d1":
        import d1 as _d1
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect()
        try:
            dump = _d1.dump_sql(conn)
        finally:
            conn.close()
        target_path.write_text(dump, encoding="utf-8")
        return str(target_path)
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    src = connect()
    try:
        dst = sqlite3.connect(str(target_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return str(target_path)


# --------------------------------------------------------------------------
# Backend selection: local SQLite file (default) or Cloudflare D1 over HTTPS
# --------------------------------------------------------------------------
BACKEND = (os.environ.get("ERP_DB_BACKEND") or "sqlite").strip().lower()


def backend_name() -> str:
    return "d1" if BACKEND == "d1" else "sqlite"


def connect():
    """Open a connection to the configured backend (SQLite file or D1)."""
    if BACKEND == "d1":
        import d1
        return d1.connect()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def is_d1() -> bool:
    return BACKEND == "d1"


@contextmanager
def transaction():
    """Unit of work.

    On a local SQLite file this is a real ``BEGIN IMMEDIATE`` transaction: every
    statement commits together or not at all. Cloudflare D1 has no interactive
    transactions over HTTPS (only atomic ``batch()``), so there the unit runs in
    autocommit and each statement is atomic on its own; anything that must not
    tear is sent through ``conn.batch([...])`` and `scripts/check_integrity.py`
    verifies the invariants afterwards.
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def read():
    return connect()


def drop_all_objects(conn) -> int:
    """Remove every user object, children before parents.

    Cloudflare D1 refuses a DROP TABLE while another table references it and
    cannot defer foreign keys, so we sweep repeatedly: each pass drops what it
    can, later passes pick up what was blocked.
    """
    dropped = 0
    for _ in range(8):
        rows = conn.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
                            "AND type IN ('view','trigger','index','table') ORDER BY "
                            "CASE type WHEN 'view' THEN 0 WHEN 'trigger' THEN 1 "
                            "WHEN 'index' THEN 2 ELSE 3 END").fetchall()
        if not rows:
            return dropped
        progress = False
        for row in rows:
            try:
                conn.execute(f'DROP {row["type"].upper()} IF EXISTS "{row["name"]}"')
                dropped += 1
                progress = True
            except Exception:
                continue
        if not progress:
            break
    return dropped


def init_db(force: bool = False) -> None:
    if BACKEND == "d1":
        conn = connect()
        if force:                       # explicit reset of a cloud database
            drop_all_objects(conn)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            conn.executescript(fh.read())
        conn.close()
        return
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
    ("allowance_lines", "att_from", "TEXT"),
    ("allowance_lines", "att_to", "TEXT"),
    ("debt_payments", "description", "TEXT"),
    ("debt_payments", "advance_cents", "INTEGER NOT NULL DEFAULT 0"),
]


def _ensure_columns(conn) -> None:
    for table, column, ddl in COLUMN_MIGRATIONS:
        try:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")} or \
                   {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except Exception:
            continue
        if not cols or column in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except Exception:
            pass


ALLOWANCE_EXPENSE_ACCOUNT = ("5215", "إعاشة العاملين", "Staff Subsistence Allowance", "52")


def _ensure_allowance_accounts(conn) -> None:
    """Add the subsistence-allowance expense account to every existing company."""
    code, name_ar, name_en, parent_code = ALLOWANCE_EXPENSE_ACCOUNT
    try:
        companies = conn.execute("SELECT id FROM companies").fetchall()
    except Exception:
        return
    for row in companies:
        cid = row["id"]
        try:
            if conn.execute("SELECT 1 FROM accounts WHERE company_id=? AND code=?", (cid, code)).fetchone():
                continue
            parent = conn.execute("SELECT id, level FROM accounts WHERE company_id=? AND code=?",
                                  (cid, parent_code)).fetchone()
            conn.execute(
                """INSERT INTO accounts (company_id, code, name_ar, name_en, type, parent_id, level,
                      is_postable, is_active, currency) VALUES (?,?,?,?,'expense',?,?,1,1,'EGP')""",
                (cid, code, name_ar, name_en, parent["id"] if parent else None,
                 (parent["level"] + 1) if parent else 2))
        except Exception:
            continue


def _apply_dark_theme_default(conn) -> None:
    """Dark mode became the product default: move untouched accounts over once.

    The switch runs a single time per company (guarded by a settings row), so a
    user who later chooses the light theme keeps it across restarts and redeploys.
    """
    try:
        companies = conn.execute("SELECT id FROM companies").fetchall()
    except Exception:
        return
    for row in companies:
        cid = row["id"]
        try:
            done = conn.execute(
                "SELECT value FROM settings WHERE company_id=? AND key='theme.dark_default'",
                (cid,)).fetchone()
            if done:
                continue
            conn.execute(
                "UPDATE users SET theme='dark' WHERE company_id=? AND (theme IS NULL OR theme='light')",
                (cid,))
            conn.execute(
                "INSERT OR REPLACE INTO settings (company_id, key, value) VALUES (?,?,?)",
                (cid, "theme.dark_default", "1"))
        except Exception:
            continue


def migrate() -> None:
    """Apply additive, idempotent schema changes to an existing database.

    Runs `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` statements
    from schema.sql, so an older database picks up new tables without a rebuild.
    """
    if BACKEND != "d1" and not DB_PATH.exists():
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
                except Exception:
                    pass
        _ensure_columns(conn)
        _sync_system_roles(conn)
        _apply_dark_theme_default(conn)
        _ensure_allowance_accounts(conn)
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
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass


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
