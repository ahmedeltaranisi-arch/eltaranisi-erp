#!/usr/bin/env python3
"""Create (or verify) an ELTARANISI ERP database on **Cloudflare D1**.

What it does
------------
1. checks the API token and finds the account (if not passed),
2. creates the D1 database when it does not exist,
3. applies `schema.sql` (47 tables, views, indexes) in atomic batches,
4. optionally seeds the demo chart of accounts / users,
5. prints the environment variables to paste into Vercel (or `.env`).

Usage
-----
    export CF_API_TOKEN=...          # token with the "D1: Edit" permission
    python3 scripts/setup_cloudflare_d1.py --account <account_id> --name eltaranisi-erp --seed

Add `--dry-run` to only report what would happen.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import d1  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=os.environ.get("CF_ACCOUNT_ID", ""))
    ap.add_argument("--name", default=os.environ.get("CF_D1_DATABASE", "eltaranisi-erp"))
    ap.add_argument("--seed", action="store_true", help="seed demo data after creating the schema")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("CF_API_TOKEN", "")
    if not token:
        print("✗ CF_API_TOKEN is not set.\n"
              "  Create one at https://dash.cloudflare.com/profile/api-tokens\n"
              "  → Create Token → Custom token → Permissions: Account · D1 · Edit")
        return 2

    client = d1.Client(account_id=args.account or "probe", token=token, base=os.environ.get("CF_D1_API_BASE"))
    if not args.account:
        body = client._request("GET", "/accounts?per_page=50")
        accounts = body.get("result") or []
        if not accounts:
            print("✗ the token cannot list accounts — pass --account <account_id>")
            return 2
        args.account = accounts[0]["id"]
        print(f"✓ account: {accounts[0].get('name') or '?'} ({args.account})")
        client.account_id = args.account

    existing = {db.get("name"): (db.get("uuid") or db.get("id")) for db in client.list_databases()}
    if args.name in existing:
        database_id = existing[args.name]
        print(f"• database already exists: {args.name} ({database_id})")
    elif args.dry_run:
        print(f"• would create database {args.name}")
        return 0
    else:
        created = client.create_database(args.name)
        database_id = created.get("uuid") or created.get("id")
        print(f"✓ created database {args.name} ({database_id})")

    client.database_id = database_id
    conn = d1.Connection(client)
    tables = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'").fetchone()["n"]
    if tables and not args.seed:
        print(f"• schema present ({tables} tables) — nothing to do")
    else:
        schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
        if args.dry_run:
            print(f"• would apply schema.sql ({len(d1._split_script(schema))} statements)")
        else:
            conn.executescript(schema)
            tables = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'").fetchone()["n"]
            print(f"✓ schema applied — {tables} tables")

    if args.seed and not args.dry_run:
        have = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] if tables else 0
        if have:
            print(f"• database already has {have} users — skipping the seed (nothing was overwritten)")
            args.seed = False
    if args.seed and not args.dry_run:
        os.environ["ERP_DB_BACKEND"] = "d1"
        os.environ["CF_ACCOUNT_ID"] = args.account
        os.environ["CF_D1_DATABASE_ID"] = database_id
        os.environ.setdefault("CF_API_TOKEN", token)
        os.environ["ERP_SEED_MODE"] = os.environ.get("ERP_SEED_MODE", "demo")
        import seed  # imported late: it binds the backend on import
        seed.seed()
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        print(f"✓ seeded — {users} users")

    print("\nAdd these to Vercel → Settings → Environment Variables (Production):")
    print(f"  ERP_DB_BACKEND=d1")
    print(f"  CF_ACCOUNT_ID={args.account}")
    print(f"  CF_D1_DATABASE_ID={database_id}")
    print(f"  CF_API_TOKEN=<the token you just created>")
    print("  ERP_EPHEMERAL_AUTH=0        (sessions are now shared and revocable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
