#!/usr/bin/env bash
# Deploy ELTARANISI ERP to Vercel.
#
#   VERCEL_TOKEN=... ./scripts/deploy-vercel.sh            # production
#   VERCEL_TOKEN=... ./scripts/deploy-vercel.sh --preview  # preview URL
#
# The token is read from the environment and never written to disk.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${VERCEL_TOKEN:-}" ]]; then
  echo "VERCEL_TOKEN is not set. Create one at https://vercel.com/account/tokens" >&2
  exit 1
fi

TARGET="--prod"
[[ "${1:-}" == "--preview" ]] && TARGET=""

# Local sanity checks first — never deploy a broken bundle.
echo "▸ running checks…"
python3 tests/test_wsgi_bridge.py > /tmp/vercel-checks.log 2>&1 || { tail -20 /tmp/vercel-checks.log; exit 1; }
python3 -c "import json;json.load(open('vercel.json'))"

echo "▸ deploying${TARGET:+ (production)}…"
npx --yes vercel@latest deploy "$TARGET" --yes --token "$VERCEL_TOKEN" --name eltaranisi-erp 2>&1 | tee /tmp/vercel-deploy.log

URL=$(grep -Eo 'https://[a-z0-9-]+\.vercel\.app' /tmp/vercel-deploy.log | tail -1)
echo
echo "▸ smoke test: ${URL:-<no url parsed>}"
if [[ -n "${URL:-}" ]]; then
  curl -sf "$URL/api/health" && echo
  curl -s "$URL/api/dev/accounts" | head -c 120; echo
fi
