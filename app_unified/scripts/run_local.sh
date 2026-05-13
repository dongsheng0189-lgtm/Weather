#!/usr/bin/env bash
# Local dev launcher: source .env.local → seed if missing → python app.py
# Safe to re-run; only seeds when dev.db does not exist (delete it to reseed).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

if [ ! -f .env.local ]; then
  echo "✗ .env.local missing — create it first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env.local
set +a

# Sanity: DATABASE_URL must be sqlite (paranoid; .env.local sets it, but if user overrode)
case "${DATABASE_URL:-}" in
  sqlite:*) ;;
  *)
    echo "✗ DATABASE_URL=${DATABASE_URL:-<unset>} is not sqlite:// — refusing to start." >&2
    echo "  Edit .env.local or unset DATABASE_URL before re-running."  >&2
    exit 1 ;;
esac

VENV_PY="/home/hirain0126/projects/webapp/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "✗ venv missing at $VENV_PY" >&2
  exit 1
fi

if [ ! -f instance/dev.db ]; then
  echo "→ instance/dev.db not found, seeding…"
  "$VENV_PY" scripts/seed_local.py
fi

# Kill stale process holding port 5000 (e.g., previous run not Ctrl-C'd)
if fuser -s 5000/tcp 2>/dev/null; then
  echo "→ Port 5000 in use, killing previous process…"
  fuser -k 5000/tcp 2>/dev/null || true
  sleep 0.5
fi

echo "→ Starting Flask dev server at http://localhost:5000 (Ctrl-C to stop)"
echo "→ Login picker: http://localhost:5000/dev"
exec "$VENV_PY" app.py
