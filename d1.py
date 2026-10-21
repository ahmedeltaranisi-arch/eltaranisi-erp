"""Cloudflare D1 backend for ELTARANISI ERP.

D1 is Cloudflare's managed **SQLite**: same SQL dialect, same types, same
`?` placeholders — but it is reached over HTTPS instead of a local file, because
a Vercel function cannot open a socket to a Cloudflare-managed file.

This module gives D1 the small slice of the `sqlite3` API the application uses:

    conn.execute(sql, params) -> cursor
    cursor.fetchone() / fetchall() / iteration / lastrowid / rowcount
    conn.executescript(sql)   (chunked into atomic batches)
    conn.execute("BEGIN IMMEDIATE" / "COMMIT" / "ROLLBACK")   (no-ops)

Configuration (environment, never hard-coded):

    CF_ACCOUNT_ID          Cloudflare account id
    CF_D1_DATABASE_ID      database uuid  (or)
    CF_D1_DATABASE         database name  (resolved once, then cached)
    CF_API_TOKEN           API token with **D1:Edit** permission
    CF_D1_API_BASE         override for the local emulator used by the tests

Design notes
------------
* **Every statement is atomic.** D1 guarantees that. A *sequence* of statements
  is not a transaction over HTTPS (Cloudflare has no interactive BEGIN/COMMIT),
  so multi-statement units use `batch()` — one atomic round-trip — and the
  application-level transaction boundary degrades to autocommit. `scripts/
  check_integrity.py` verifies the invariants this could otherwise break.
* `INSERT ... RETURNING id` is preferred for new code: D1 returns the row.
  Older code uses `cursor.lastrowid`, which D1 reports in `meta.last_row_id`.
* Rows come back as JSON objects; they are wrapped in a `Row` that behaves like
  `sqlite3.Row` (mapping access, `.keys()`, integer index) so no caller changes.
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.cloudflare.com/client/v4"


# --------------------------------------------------------------------------
# errors — shaped like sqlite3 so existing `except sqlite3.Error` blocks work
# --------------------------------------------------------------------------
try:                                   # so existing `except sqlite3.Error` catches us
    import sqlite3 as _sqlite3
    _ERROR_BASE = _sqlite3.Error
except Exception:                      # pragma: no cover
    _ERROR_BASE = Exception


class D1Error(_ERROR_BASE):
    """Base error — is-a sqlite3.Error, so no caller has to change."""


class D1OperationalError(D1Error):
    pass


class D1IntegrityError(D1Error):
    pass


def _classify(message: str, exc_type) -> D1Error:
    msg = str(message or "")
    low = msg.lower()
    if "unique constraint" in low or "not null constraint" in low or "foreign key" in low:
        return D1IntegrityError(msg)
    return D1OperationalError(msg)


# --------------------------------------------------------------------------
# row / cursor
# --------------------------------------------------------------------------
class Row:
    """`sqlite3.Row` work-alike over a JSON object."""

    __slots__ = ("_names", "_values", "_index")

    def __init__(self, names, values):
        self._names = names
        self._values = values
        self._index = {n: i for i, n in enumerate(names)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def __repr__(self):
        return f"Row({dict(zip(self._names, self._values))})"


class Cursor:
    def __init__(self, rows, changes=0, last_row_id=0):
        self._rows = rows
        self._pos = 0
        self.rowcount = changes
        self.lastrowid = last_row_id

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def fetchmany(self, size=1):
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self):
        self._rows = []


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------
class Client:
    def __init__(self, account_id=None, database_id=None, token=None, base=None,
                 timeout=30, retries=3):
        self.account_id = account_id or os.environ.get("CF_ACCOUNT_ID", "")
        self.token = token or os.environ.get("CF_API_TOKEN", "")
        self.base = (base or os.environ.get("CF_D1_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.database_id = database_id or os.environ.get("CF_D1_DATABASE_ID", "")
        self._database_name = os.environ.get("CF_D1_DATABASE", "")
        if not self.account_id or not self.token:
            raise D1OperationalError(
                "CF_ACCOUNT_ID and CF_API_TOKEN are required for the D1 backend")
        self.lazy_database = not self.database_id
        if not self.database_id and self._database_name:
            self.database_id = self._resolve_database()

    # -- low level ---------------------------------------------------------
    def _request(self, method, path, payload=None):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        last = None
        for attempt in range(self.retries):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode() or "{}")
            except urllib.error.HTTPError as exc:
                body = {}
                try:
                    body = json.loads(exc.read().decode() or "{}")
                except Exception:
                    pass
                if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < self.retries:
                    time.sleep(0.4 * (2 ** attempt) + random.random() * 0.2)
                    last = _classify(json.dumps(body), exc)
                    continue
                errors = body.get("errors") or [{"message": f"HTTP {exc.code}"}]
                raise _classify(errors[0].get("message"), exc)
            except urllib.error.URLError as exc:
                last = D1OperationalError(f"network error: {exc.reason}")
                if attempt + 1 < self.retries:
                    time.sleep(0.4 * (2 ** attempt))
                    continue
                raise last
            if not body.get("success", True):
                errors = body.get("errors") or [{"message": "D1 request failed"}]
                raise _classify(errors[0].get("message"), None)
            return body
        raise last or D1OperationalError("D1 request failed")

    def _resolve_database(self) -> str:
        name = self._database_name
        if not name:
            raise D1OperationalError("CF_D1_DATABASE_ID or CF_D1_DATABASE is required")
        body = self._request("GET", f"/accounts/{self.account_id}/d1/database?per_page=100")
        for db in body.get("result") or []:
            if db.get("name") == name:
                return db.get("uuid") or db.get("id")
        raise D1OperationalError(f"D1 database '{name}' not found in account {self.account_id}")

    # -- API ---------------------------------------------------------------
    def _ensure_database(self):
        if not self.database_id:
            raise D1OperationalError("CF_D1_DATABASE_ID or CF_D1_DATABASE is required")

    def query(self, sql, params=()):
        self._ensure_database()
        body = self._request("POST",
                             f"/accounts/{self.account_id}/d1/database/{self.database_id}/query",
                             {"sql": sql, "params": list(params or ())})
        return _unwrap(body)

    def batch(self, statements):
        """Run [(sql, params), ...] atomically — all or nothing."""
        self._ensure_database()
        body = self._request("POST",
                             f"/accounts/{self.account_id}/d1/database/{self.database_id}/batch",
                             {"batch": [{"sql": s, "params": list(p or ())} for s, p in statements]})
        result = body.get("result") or []
        return [_unwrap_one(item) for item in result]

    def list_databases(self):
        return self._request("GET", f"/accounts/{self.account_id}/d1/database?per_page=100").get("result") or []

    def create_database(self, name):
        body = self._request("POST", f"/accounts/{self.account_id}/d1/database", {"name": name})
        result = body.get("result") or {}
        self.database_id = result.get("uuid") or result.get("id")
        return result

    def raw_export(self):
        return self._request("POST",
                             f"/accounts/{self.account_id}/d1/database/{self.database_id}/export",
                             {"output_format": "polling"})


def _unwrap_one(item: dict):
    """D1 answer -> (rows, changes, last_row_id)."""
    rows = []
    for raw in item.get("results") or []:
        if isinstance(raw, dict):
            rows.append(Row(list(raw.keys()), list(raw.values())))
        else:
            rows.append(Row(["value"], [raw]))
    meta = item.get("meta") or {}
    return rows, int(meta.get("changes") or 0), int(meta.get("last_row_id") or 0)


def _unwrap(body: dict):
    result = body.get("result") or []
    if isinstance(result, dict):                     # single-object answers
        result = [result]
    if not result:
        return [], 0, 0
    return _unwrap_one(result[0])


# --------------------------------------------------------------------------
# connection
# --------------------------------------------------------------------------
class Connection:
    """sqlite3.Connection work-alike backed by D1 over HTTPS."""

    def __init__(self, client=None):
        self.client = client or Client()
        self.closed = False
        self.last_changes = 0

    # -- statements --------------------------------------------------------
    def execute(self, sql, params=()):
        text = (sql or "").strip()
        head = text[:6].upper()
        if head in ("BEGIN ", "COMMIT", "ROLLBA") or text.upper() in ("BEGIN", "BEGIN;", "COMMIT;", "ROLLBACK;"):
            return Cursor([])                       # no interactive transactions on D1
        if head == "PRAGMA" and "=" in text:
            return Cursor([])                       # journal_mode = WAL etc. — server managed
        rows, changes, last_id = self.client.query(text, params)
        self.last_changes = changes
        return Cursor(rows, changes, last_id)

    def executemany(self, sql, seq):
        statements = [(sql, params) for params in seq]
        results = self.client.batch(statements)
        return Cursor([r[0][0] if r[0] else None for r in results])

    def executescript(self, script):
        statements = _split_script(script)
        for start in range(0, len(statements), 50):
            chunk = statements[start:start + 50]
            self.client.batch([(s, ()) for s in chunk])

    def batch(self, statements):
        """Atomic multi-statement unit — use for anything that must not tear."""
        results = self.client.batch(statements)
        out = []
        for rows, changes, last_id in results:
            out.append(Cursor(rows, changes, last_id))
        return out

    # -- housekeeping ------------------------------------------------------
    def commit(self):
        pass                                        # autocommit

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _split_script(script: str):
    """Split a .sql file into statements (comments and string literals safe)."""
    statements, buf, i, n = [], [], 0, len(script or "")
    quote = None
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "-" and nxt == "-":
            while i < n and script[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def connect() -> Connection:
    return Connection()


# --------------------------------------------------------------------------
# logical backup / restore (D1 has no file to copy)
# --------------------------------------------------------------------------
def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def dump_sql(conn, batch_size=500) -> str:
    """Whole database as portable SQL (schema + data) — the D1 backup format."""
    lines = ["-- ELTARANISI ERP — Cloudflare D1 logical backup",
             "-- restore:  python3 scripts/restore_backup.py <this-file>.sql", ""]
    objects = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table','index','view','trigger') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
    ).fetchall()
    tables = [r["name"] for r in objects if r["type"] == "table"]
    for row in objects:
        if row["type"] == "table" and row["sql"]:
            lines.append(row["sql"].rstrip(";") + ";")
    for table in tables:
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        for start in range(0, len(rows), batch_size):
            for r in rows[start:start + batch_size]:
                cols = ", ".join(f'"{c}"' for c in r.keys())
                vals = ", ".join(_sql_literal(v) for v in list(r))
                lines.append(f'INSERT INTO "{table}" ({cols}) VALUES ({vals});')
    for row in objects:
        if row["type"] in ("index", "view", "trigger") and row["sql"]:
            lines.append(row["sql"].rstrip(";") + ";")
    return "\n".join(lines) + "\n"
