#!/usr/bin/env bash
# Consistent SQLite backup (online-safe): VACUUM INTO snapshot, then compress,
# optionally encrypt with age/gpg. Retention: 30 days by default.
set -euo pipefail

DB_PATH="${ERP_DB:-./data/erp.db}"
OUT_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$OUT_DIR"
SNAP="$OUT_DIR/erp-$STAMP.db"

python3 - "$DB_PATH" "$SNAP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(src)
con.execute("VACUUM INTO ?", (dst,))
con.close()
print("snapshot written:", dst)
PY

gzip -f "$SNAP"
ARCHIVE="$SNAP.gz"

if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
  if command -v age >/dev/null 2>&1; then
    age -r "$BACKUP_AGE_RECIPIENT" -o "$ARCHIVE.age" "$ARCHIVE"
    rm -f "$ARCHIVE"
    ARCHIVE="$ARCHIVE.age"
  else
    echo "age not installed — archive left unencrypted" >&2
  fi
elif [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
  gpg --yes --encrypt --recipient "$BACKUP_GPG_RECIPIENT" "$ARCHIVE"
  rm -f "$ARCHIVE"
  ARCHIVE="$ARCHIVE.gpg"
fi

sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
find "$OUT_DIR" -type f -name 'erp-*' -mtime "+$RETENTION_DAYS" -delete
echo "backup complete: $ARCHIVE"
