#!/usr/bin/env bash
#
# Production start script (Railpack / Railway entrypoint).
#
# Launches the FastAPI backend. Railway injects $PORT; we fall back to 8000
# for local runs (`./start.sh`).
#
# Railpack's Shell provider runs this file. If the backend deps aren't already
# present in the image, we install them on first boot.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT/backend"

PORT="${PORT:-8000}"

# Install deps if uvicorn isn't importable yet (no-op when the build already did it).
if ! python -c "import uvicorn" >/dev/null 2>&1; then
  echo "[start] Installing backend dependencies…"
  python -m pip install --no-cache-dir -r requirements.txt
fi

echo "[start] Backend → 0.0.0.0:${PORT}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
