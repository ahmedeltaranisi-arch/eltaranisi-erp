#!/usr/bin/env bash
# Restore a backup produced by backup.sh. Stops the service first in real deployments.
set -euo pipefail
SRC="${1:?usage: restore.sh <backup-file.gz|.gz.age|.gz.gpg>}"
DB_PATH="${ERP_DB:-./data/erp.db}"
TMP=$(mktemp -d)

case "$SRC" in
  *.age) age -d -i "${AGE_IDENTITY:?set AGE_IDENTITY to your private key}" -o "$TMP/db.gz" "$SRC" ;;
  *.gpg) gpg --yes -o "$TMP/db.gz" -d "$SRC" ;;
  *.gz)  cp "$SRC" "$TMP/db.gz" ;;
  *) echo "unsupported archive type" >&2; exit 1 ;;
esac

gunzip -f "$TMP/db.gz"
python3 - "$TMP/db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print("integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
print("journal entries:", con.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0])
PY
mv "$TMP/db" "$DB_PATH"
rm -rf "$TMP"
echo "restored into $DB_PATH — restart the service"
