"""Vercel entrypoint for ELTARANISI ERP.

The application itself is a plain `http.server` app (`server.py`) — no framework.
This module is a thin **WSGI bridge**: it rebuilds an HTTP request from the WSGI
environment, runs it through the *unchanged* request handler, and turns the raw
HTTP response back into a WSGI response. Nothing in the business logic moves, so
the serverless deployment behaves exactly like `python3 server.py`.

Why a bridge instead of a rewrite?  `server.py` is battle-tested (34 integration
tests, byte-exact PDF/Excel exports) and `http.server` is part of the stdlib; a
rewrite to Flask/FastAPI would add dependencies and risk. Vercel's Python runtime
speaks WSGI, so a ~70-line adapter is enough.

Serverless constraints handled here
-----------------------------------
* Filesystem is read-only except ``/tmp``  → the SQLite file lives in ``/tmp``.
* Instances are ephemeral                  → the database is seeded on cold start
                                             (~0.5 s) and marked demo-only.
* Routes are rewritten to ``/api/index``   → the original API path travels in the
                                             ``__p`` query parameter.
"""
from __future__ import annotations

import io
import os
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------------------
# 1) Storage bootstrap — must happen BEFORE `db` is imported (it caches the path)
# --------------------------------------------------------------------------
ON_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
D1_BACKEND = (os.environ.get("ERP_DB_BACKEND") or "").strip().lower() == "d1"
if ON_VERCEL and not D1_BACKEND:
    os.environ.setdefault("ERP_DB", "/tmp/eltaranisi-erp/erp.db")
    os.environ.setdefault("ERP_CREDENTIALS_FILE", "/tmp/eltaranisi-erp/dev-credentials.txt")
    os.makedirs("/tmp/eltaranisi-erp", exist_ok=True)

# Demo login listing is CLOSED by default on serverless: the deployment URL is
# public, so it must not advertise credentials. Flip it on for a showcase with
#   ERP_SEED_DEMO_DATA=true
# (the seeded users still exist; they just are not listed publicly).
os.environ.setdefault("ERP_SEED_DEMO_DATA", "false")

# Each serverless instance owns its own /tmp database, so a session row written by
# one instance does not exist in the next one. Signed bearer tokens are therefore
# accepted without the row (revocation still applies wherever the row is present).
# With a shared database (Cloudflare D1) the session table is global, so real
# server-side revocation is enforced again.
os.environ.setdefault("ERP_EPHEMERAL_AUTH", "0" if D1_BACKEND else "1")

import db  # noqa: E402  (import after ERP_DB is set)

_SEED_LOCK = threading.Lock()


def ensure_database() -> None:
    """Create + seed the SQLite file once per serverless instance.

    With the Cloudflare D1 backend there is nothing local to create: the database
    is provisioned once by `scripts/setup_cloudflare_d1.py` and shared by every
    instance, so a cold start only needs to check the schema is reachable.
    """
    if D1_BACKEND:
        # One cheap query instead of the full ~84-statement migration on every
        # cold start (each statement is an HTTPS round-trip to D1).
        try:
            db.ensure_schema()           # runs migrate() only if schema.sql changed
        except Exception:
            traceback.print_exc()
        return
    if db.DB_PATH.exists():
        return
    with _SEED_LOCK:
        if db.DB_PATH.exists():
            return
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        import seed as seed_module

        seed_module.seed()
        try:
            db.ensure_schema()                # additive migrations, only when needed
        except Exception:
            traceback.print_exc()


try:
    ensure_database()
except Exception:                                    # pragma: no cover
    traceback.print_exc()                            # surfaced in Vercel logs

try:
    import server                                    # noqa: E402
except Exception:                                    # pragma: no cover - deployment guard
    traceback.print_exc()
    server = None

# --------------------------------------------------------------------------
# 2) WSGI → BaseHTTPRequestHandler bridge
# --------------------------------------------------------------------------
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailer", "transfer-encoding", "upgrade"}

ROUTE_FORWARDING_BROKEN = "/api/__route_not_forwarded__"


class _Socket:
    """Minimal stand-in for a socket: `makefile` is all http.server needs."""

    def __init__(self, rfile: io.BytesIO, wfile: io.BytesIO):
        self._rfile, self._wfile = rfile, wfile

    def makefile(self, mode="rb", *args, **kwargs):
        return self._rfile if "r" in mode else self._wfile

    # socketserver.StreamRequestHandler (wbufsize=0) writes through
    # _SocketWriter → connection.sendall, so the shim must provide it.
    def sendall(self, data, *args, **kwargs):
        self._wfile.write(data)
        return len(data)

    def send(self, data, *args, **kwargs):
        self._wfile.write(data)
        return len(data)

    def flush(self):
        self._wfile.flush()

    def close(self):
        pass

    def shutdown(self, *args, **kwargs):
        pass


def _original_path(environ: dict) -> tuple[str, str]:
    """Recover the real request path/query after Vercel's rewrite."""
    query = environ.get("QUERY_STRING", "")
    params = {}
    if query:
        import urllib.parse
        for key, values in urllib.parse.parse_qs(query, keep_blank_values=True).items():
            params.setdefault(key, list(values))
    if "__p" in params:
        tail = params.pop("__p")[0].lstrip("/")
        path = "/api/" + tail
        # merge back any query parameters that survived the rewrite
        rest = urllib.parse.urlencode({k: v[0] for k, v in params.items()})
        return path, rest
    path = environ.get("PATH_INFO", "/") or "/"
    if path.startswith("/api/index"):
        # the rewrite dropped the `__p` parameter — signal it explicitly so the
        # bridge can answer with an actionable diagnostic instead of a bare 404.
        return ROUTE_FORWARDING_BROKEN, query
    return path, query


def _build_request(environ: dict, body: bytes, path: str, query: str):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    url = path + (("?" + query) if query else "")
    headers = []
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            if name.lower() in HOP_BY_HOP:
                continue
            headers.append(f"{name}: {value}")
    if environ.get("CONTENT_TYPE"):
        headers.append(f"Content-Type: {environ['CONTENT_TYPE']}")
    headers.append(f"Content-Length: {len(body) if body else 0}")
    headers.append("Connection: close")
    headers.append(f"Host: {environ.get('HTTP_HOST') or environ.get('SERVER_NAME', 'localhost')}")
    raw = f"{method} {url} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
    return raw.encode("latin-1", "replace") + body


def _client_ip(environ: dict) -> str:
    return (environ.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or environ.get("REMOTE_ADDR") or "127.0.0.1")


def _split_response(raw: bytes) -> tuple[str, list[tuple[str, str]], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 2)
    status_line = " ".join(status[1:]).strip() if len(status) > 1 else "200 OK"
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip().lower() in HOP_BY_HOP:
            continue
        headers.append((name.strip(), value.strip()))
    return status_line, headers, body


def _broken_app(environ, start_response):
    """Readable diagnostic when the deployment bundle is missing project files."""
    payload = (
        b'{"error": "DEPLOYMENT_INCOMPLETE: server.py could not be imported. '
        b'Check includeFiles in vercel.json (*.py, schema.sql, assets/**)."}'
    )
    start_response("500 Internal Server Error",
                   [("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(payload)))])
    return [payload]


def app(environ, start_response):
    """WSGI entrypoint."""
    if server is None:
        return _broken_app(environ, start_response)
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length else b""

    path, query = _original_path(environ)
    if path == ROUTE_FORWARDING_BROKEN:
        payload = (
            b'{"error": "ROUTE_NOT_FORWARDED: vercel.json rewrite did not pass __p. '
            b'Expected: /api/:path* -> /api/index?__p=:path*"}'
        )
        start_response("502 Bad Gateway",
                       [("Content-Type", "application/json; charset=utf-8"),
                        ("Content-Length", str(len(payload))),
                        ("X-Route-Debug", "no-__p-parameter")])
        return [payload]

    rfile = io.BytesIO(_build_request(environ, body, path, query))
    wfile = io.BytesIO()

    try:
        server.Handler(_Socket(rfile, wfile), (_client_ip(environ), 0), None)
    except Exception:
        traceback.print_exc()
        payload = b'{"error": "INTERNAL_ERROR: serverless bridge failed / \xd8\xae\xd8\xb7\xd8\xa3 \xd9\x81\xd9\x8a \xd8\xa7\xd9\x84\xd8\xaa\xd8\xb4\xd8\xba\xd9\x8a\xd9\x84"}'
        start_response("500 Internal Server Error",
                       [("Content-Type", "application/json; charset=utf-8"),
                        ("Content-Length", str(len(payload)))])
        return [payload]

    status_line, headers, out = _split_response(wfile.getvalue())
    if status_line.startswith("HTTP/"):          # defensive: never forward the protocol line
        status_line = status_line.split(" ", 1)[1]
    if not any(name.lower() == "content-length" for name, _ in headers):
        headers.append(("Content-Length", str(len(out))))
    start_response(status_line, headers)
    if environ.get("REQUEST_METHOD", "GET").upper() == "HEAD":
        return [b""]
    return [out]
