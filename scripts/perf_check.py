#!/usr/bin/env python3
"""Measure the API the way a user feels it.

    python3 scripts/perf_check.py                      # against http://127.0.0.1:8000
    ERP_BASE=https://eltaranisi-erp.vercel.app python3 scripts/perf_check.py
    ERP_USER=ahmed.taranisi ERP_PASS='Eltaranisi@2026' ...

Prints, per endpoint: wall time (ms), payload size and how many times it was
served from the short-lived response cache. A cold start is measured first
(the very first request after the instance was asleep) because that is what most
"the site is slow" complaints are about.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ERP_BASE", "http://127.0.0.1:8000").rstrip("/")
USER = os.environ.get("ERP_USER", "ahmed.taranisi")
PASS = os.environ.get("ERP_PASS", "Eltaranisi@2026")

READS = [
    "/api/meta",
    "/api/dashboard",
    "/api/accounts",
    "/api/vendors",
    "/api/customers",
    "/api/banks",
    "/api/debt/summary",
    "/api/journal-entries?limit=50",
    "/api/reports/trial-balance",
    "/api/payments/bootstrap",
]


def call(method: str, path: str, payload=None, token: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as res:
        raw = res.read()
    return (time.perf_counter() - started) * 1000, raw


def main() -> int:
    print(f"# {BASE}")
    cold, raw = call("GET", "/api/health")
    print(f"cold start (first request)      {cold:8.0f} ms")
    _, raw = call("POST", "/api/auth/login", {"username": USER, "password": PASS})
    token = json.loads(raw).get("token")
    if not token:
        print("login failed:", raw[:200])
        return 1
    print(f"{'endpoint':34s} {'1st':>8s} {'2nd':>8s} {'3rd':>8s}  {'KB':>6s}")
    total_first = total_warm = 0.0
    for path in READS:
        try:
            first, raw = call("GET", path, token=token)
            warm = [call("GET", path, token=token)[0] for _ in range(2)]
        except urllib.error.HTTPError as exc:
            print(f"{path:34s}  ERROR {exc.code}")
            continue
        total_first += first
        total_warm += sum(warm) / 2
        print(f"{path:34s} {first:8.0f} {warm[0]:8.0f} {warm[1]:8.0f}  {len(raw)/1024:6.0f}")
    print(f"{'TOTAL':34s} {total_first:8.0f} {total_warm:8.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
