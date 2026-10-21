#!/usr/bin/env python3
"""Local stand-in for the Cloudflare D1 REST API (development / CI only).

It speaks the same routes and the same JSON envelope as Cloudflare, backed by a
local SQLite file, so the application can be exercised end-to-end with
`ERP_DB_BACKEND=d1` before a real account is touched.

    python3 tools/d1_emulator.py --port 8787 --db /tmp/d1-emu.db

    export CF_D1_API_BASE=http://127.0.0.1:8787/client/v4
    export CF_ACCOUNT_ID=local CF_API_TOKEN=local CF_D1_DATABASE=eltaranisi-erp
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path("/tmp/d1-emu.db")
DATABASES: dict[str, str] = {}          # uuid -> sqlite file


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_statement(path: Path, sql: str, params):
    conn = _connect(path)
    try:
        cur = conn.execute(sql, tuple(params or ()))
        rows = []
        if cur.description:
            for row in cur.fetchmany(10000):
                rows.append({k: row[k] for k in row.keys()})
        last_id = cur.lastrowid if sql.lstrip()[:6].upper().startswith("INSERT") else 0
        return {
            "results": rows,
            "success": True,
            "meta": {"changes": cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0,
                     "last_row_id": last_id or 0, "rows_read": len(rows), "rows_written": 0},
        }
    finally:
        conn.close()


def run_batch(path: Path, statements):
    conn = _connect(path)
    out = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in statements:
            cur = conn.execute(item.get("sql") or "", tuple(item.get("params") or ()))
            rows = []
            if cur.description:
                rows = [{k: r[k] for k in r.keys()} for r in cur.fetchall()]
            sql = (item.get("sql") or "").lstrip()[:6].upper()
            out.append({"results": rows, "success": True,
                        "meta": {"changes": max(cur.rowcount, 0) if sql == "UPDATE" or sql.startswith("DELETE") else 0,
                                 "last_row_id": cur.lastrowid if sql == "INSERT" else 0}})
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        conn.execute("ROLLBACK")
        raise exc
    finally:
        conn.close()
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):        # keep the console readable
        pass

    def _send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode() or "{}")

    def _db_path(self, uuid):
        path = DATABASES.get(uuid)
        if not path:
            self._send(404, {"success": False, "errors": [{"message": f"database {uuid} not found"}]})
            return None
        return Path(path)

    def do_GET(self):
        self.path = self.path.split("?")[0]
        if re.search(r"/accounts$", self.path):
            return self._send(200, {"success": True, "errors": [],
                                    "result": [{"id": "local", "name": "Local (emulator)"}]})
        if re.search(r"/d1/database$", self.path):
            result = [{"uuid": u, "name": Path(p).stem} for u, p in DATABASES.items()]
            return self._send(200, {"success": True, "result": result, "errors": []})
        m = re.search(r"/d1/database/([^/?]+)$", self.path)
        if m:
            path = self._db_path(m.group(1))
            if path:
                self._send(200, {"success": True, "result": {"uuid": m.group(1), "name": path.stem}})
            return
        self._send(404, {"success": False, "errors": [{"message": "not found"}]})

    def do_POST(self):
        if re.search(r"/d1/database$", self.path):
            body = self._body()
            name = body.get("name") or "db"
            uuid = f"local-{len(DATABASES) + 1}"
            path = DB_PATH.parent / f"{name.replace('/', '-')}.db"
            DATABASES[uuid] = str(path)
            _connect(path).close()
            return self._send(200, {"success": True, "result": {"uuid": uuid, "name": name}})

        m = re.search(r"/d1/database/([^/?]+)/(query|batch|raw)$", self.path)
        if not m:
            return self._send(404, {"success": False, "errors": [{"message": "not found"}]})
        uuid, action = m.group(1), m.group(2)
        path = self._db_path(uuid)
        if path is None:
            return
        try:
            body = self._body()
            if action == "query":
                result = [run_statement(path, body.get("sql") or "", body.get("params") or [])]
            else:
                result = run_batch(path, body.get("batch") or [])
            self._send(200, {"success": True, "result": result, "errors": []})
        except sqlite3.Error as exc:
            self._send(400, {"success": False, "result": [],
                             "errors": [{"message": str(exc), "code": 7500}]})


def main():
    global DB_PATH, DATABASES
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--db", default="/tmp/d1-emu.db")
    args = ap.parse_args()
    DB_PATH = Path(args.db)
    DATABASES["local-1"] = str(DB_PATH)
    _connect(DB_PATH).close()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"D1 emulator on http://127.0.0.1:{args.port}/client/v4  (db {DB_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
