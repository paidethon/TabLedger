#!/usr/bin/env bash
# Start a disposable TabLedger instance for the E2E suite and run Playwright.
# Usage: scripts/run_e2e_server.sh
# The bootstrap password is randomly generated per run and written to the
# gitignored web/e2e/.e2e-password; no credential is stored in this repo.
set -euo pipefail
cd "$(dirname "$0")/.."

DATA_DIR="$(mktemp -d /tmp/tabledger-e2e.XXXXXX)"
PORT="${TAB_E2E_PORT:-8766}"
PASSWORD_FILE="$(pwd)/web/e2e/.e2e-password"

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  rm -f "$PASSWORD_FILE"
  rm -rf "$DATA_DIR"
}
trap 'cleanup' EXIT

# Prefer the project venv when present (local dev); CI uses system python.
if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi

# Random one-off password for the throwaway instance.
"$PYTHON" -c 'import secrets; print("".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(16)))' > "$PASSWORD_FILE"

export TAB_DATA_DIR="$DATA_DIR"
export TAB_BOOTSTRAP_ADMIN=admin
export TAB_BOOTSTRAP_PASSWORD
TAB_BOOTSTRAP_PASSWORD="$(cat "$PASSWORD_FILE")"
export TAB_COOKIE_NAME=tab_session
export TAB_INSECURE_COOKIES=1
export TAB_SECRET_KEY=e2e-secret
"$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$PORT/api/v1/health" >/dev/null; then break; fi
  sleep 0.2
done
echo "E2E server on :$PORT (data: $DATA_DIR)"

cd web
pnpm exec playwright install chromium >/dev/null 2>&1 || true
TAB_E2E_URL="http://127.0.0.1:$PORT" pnpm exec playwright test "$@"
