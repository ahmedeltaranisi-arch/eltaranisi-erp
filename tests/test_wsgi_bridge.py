"""Offline verification of the Vercel WSGI bridge (`api/index.py`).

No HTTP server is started: requests go straight into the WSGI app, exactly the
way Vercel invokes it. Run with:

    python3 tests/test_wsgi_bridge.py

It exercises the real code path (routing → auth → SQLite → PDF export) and the
serverless-specific bits: /tmp database bootstrap, credentials fallback and the
`__p` rewrite parameter.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="erp-vercel-"))
os.environ["ERP_DB"] = str(TMP / "erp.db")
os.environ["ERP_CREDENTIALS_FILE"] = str(TMP / "dev-credentials.txt")
os.environ["ERP_SEED_DEMO_DATA"] = "true"

sys.path.insert(0, str(ROOT / "api"))
import index as wsgi_app  # noqa: E402  (must come after the env vars)

PASS, FAIL = 0, 0


def call(method, path, body=b"", headers=None, rewrite=True, raw_path=None):
    """Invoke the WSGI app the way Vercel does (rewrite → /api/index?__p=...)."""
    headers = headers or {}
    if rewrite and path.startswith("/api/"):
        environ_path, _, query = path.partition("?")
        tail = environ_path[len("/api/"):]
        query = f"__p={tail}" + (f"&{query}" if query else "")
    else:
        environ_path, _, query = path.partition("?")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": raw_path or ("/api/index" if (rewrite and path.startswith("/api/")) else environ_path),
        "QUERY_STRING": query,
        "CONTENT_TYPE": "application/json" if body else "",
        "CONTENT_LENGTH": str(len(body)) if body else "",
        "SERVER_NAME": "localhost",
        "HTTP_HOST": "erp.vercel.app",
        "HTTP_X_FORWARDED_FOR": "203.0.113.7",
        "wsgi.input": io.BytesIO(body),
        "wsgi.url_scheme": "https",
    }
    for name, value in headers.items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    chunks = wsgi_app.app(environ, start_response)
    payload = b"".join(chunks)
    return int(captured["status"].split()[0]), payload, captured["headers"]


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def closed_mode_check():
    """Second pass with ERP_SEED_DEMO_DATA=false — the login listing must disappear."""
    global PASS, FAIL
    print("\nClosed-mode check (ERP_SEED_DEMO_DATA=false)")
    os.environ["ERP_SEED_DEMO_DATA"] = "false"   # flags are read per request
    status, body, _ = call("GET", "/api/health")
    check("health reports dev_mode = false", status == 200 and b'"dev_mode": false' in body)
    status, body, _ = call("GET", "/api/dev/accounts")
    check("GET /api/dev/accounts → 404 (no public credential listing)", status == 404)
    import json
    creds = json.dumps({"username": "ahmed.taranisi", "password": "Eltaranisi@2026"}).encode()
    status, body, _ = call("POST", "/api/auth/login", creds)
    check("seeded owner can still sign in manually", status == 200 and bool(json.loads(body).get("token")))
    os.environ["ERP_SEED_DEMO_DATA"] = "true"    # restore for any later check


def main():
    d1_backend = (os.environ.get("ERP_DB_BACKEND") or "").lower() == "d1"
    print("Vercel WSGI bridge — offline verification")
    print(f"  backend : {'cloudflare-d1' if d1_backend else 'sqlite'}")
    print(f"  database: {os.environ.get('CF_D1_DATABASE') or os.environ['ERP_DB']}")

    # 1. cold start actually reached a seeded database
    if d1_backend:
        import db
        conn = db.connect()
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        tables = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'").fetchone()["n"]
        conn.close()
        check("cold start reached the shared D1 database (no local file)", True)
        check("D1 database has the full schema", tables >= 40, f"({tables} tables)")
        check("D1 database is seeded with real users", users >= 7, f"({users} users)")
        check("no local SQLite file was created for the D1 backend",
              not Path(os.environ["ERP_DB"]).exists())
    else:
        check("cold start created the SQLite file", Path(os.environ["ERP_DB"]).exists())
        check("cold start wrote demo credentials", Path(os.environ["ERP_CREDENTIALS_FILE"]).exists())
        size = Path(os.environ["ERP_DB"]).stat().st_size
        check("database is seeded with real data (>500 KB)", size > 500_000, f"({size} bytes)")

    # 2. health + public demo accounts
    status, body, headers = call("GET", "/api/health")
    check("GET /api/health → 200 JSON", status == 200 and b'"status": "ok"' in body)
    status, body, headers = call("GET", "/api/dev/accounts")
    check("GET /api/dev/accounts exposes 7 demo logins",
          status == 200 and body.count(b'"username"') == 7)

    # 3. auth guard
    status, body, headers = call("GET", "/api/meta")
    check("unauthenticated /api/meta → 401", status == 401, f"(got {status})")

    # 4. login through the bridge
    import json
    creds = json.dumps({"username": "ahmed.taranisi", "password": "Eltaranisi@2026"}).encode()
    status, body, headers = call("POST", "/api/auth/login", creds)
    token = json.loads(body).get("token") if status == 200 else None
    check("POST /api/auth/login → token", status == 200 and bool(token))
    auth = {"Authorization": f"Bearer {token}"}

    # 5. authenticated reads
    status, body, headers = call("GET", "/api/meta", headers=auth)
    check("authenticated /api/meta → 200", status == 200)
    status, body, _ = call("GET", "/api/dashboard", headers=auth)
    check("GET /api/dashboard → 200", status == 200)
    status, body, _ = call("GET", "/api/health", headers=auth)
    if d1_backend:
        check("health reports a durable store (D1)", status == 200 and b'"durable": true' in body,
              f"({body[:160]!r})")
    else:
        check("health reports the storage verdict", status == 200 and b'"durable"' in body)

    # 6. binary document export (PDF) through the bridge
    status, body, headers = call("GET", "/api/sales-invoices/54/pdf?lang=ar", headers=auth)
    check("invoice PDF export → 200 application/pdf",
          status == 200 and headers.get("Content-Type") == "application/pdf", f"(got {status})")
    check("PDF bytes survive the bridge", body.startswith(b"%PDF-1.7") and len(body) > 150_000,
          f"({len(body)} bytes)")
    check("Content-Length matches the body", int(headers.get("Content-Length", 0)) == len(body))

    # 7. Excel export keeps the RTL workbook flag
    status, body, _ = call("GET", "/api/sales-invoices/54/xlsx", headers=auth)
    check("invoice XLSX export → 200", status == 200 and body.startswith(b"PK"))

    # 8. query strings survive the rewrite (filters must reach the report)
    status, body, _ = call("GET", "/api/reports/trial-balance?from=2026-01-01&to=2026-12-31", headers=auth)
    check("query parameters survive the __p rewrite",
          status == 200 and b'"balanced": true' in body, f"(got {status})")

    # 9. 404s and errors stay JSON
    status, body, _ = call("GET", "/api/does-not-exist", headers=auth)
    check("unknown API route → 404 JSON", status == 404 and b'"error"' in body)

    # 10. static assets never reach the function (Vercel's CDN serves static/)
    status, body, _ = call("GET", "/index.html", rewrite=False)
    check("static assets bypass the rewrite (served by the CDN, function is a fallback)",
          status in (200, 404), f"(got {status})")

    # 11. misconfigured rewrite produces an actionable diagnostic, not a blank 404
    status, body, headers = call("GET", "/api/health", rewrite=False, raw_path="/api/index")
    check("missing __p → 502 with ROUTE_NOT_FORWARDED diagnostic",
          status == 502 and b"ROUTE_NOT_FORWARDED" in body
          and headers.get("X-Route-Debug") == "no-__p-parameter")

    if os.environ.get("ERP_SEED_DEMO_DATA") == "true":
        closed_mode_check()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
