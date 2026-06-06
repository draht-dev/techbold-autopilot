#!/usr/bin/env bash
#
# One-command local dev runner for the AI Service Desk Autopilot.
#
#   ./dev.sh           start backend (:8000) + frontend (:5173)
#   ./dev.sh --mock    also start the mock Phoenix ERP (:9000)
#
# Reads credentials from ./.env (see .env.example). Creates the backend venv
# and installs frontend deps on first run. Ctrl-C stops everything.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PY="$BACKEND/.venv/bin/python"

WITH_MOCK=0
for arg in "$@"; do
  [ "$arg" = "--mock" ] && WITH_MOCK=1
done

# --- backend venv (Python 3.11–3.13; NOT 3.14 — wheel builds fail) ----------
if [ ! -x "$VENV_PY" ]; then
  echo "[dev] Creating backend venv…"
  PY="$(command -v python3.12 || command -v python3.13 || command -v python3.11)"
  if [ -z "$PY" ]; then
    echo "[dev] ERROR: need python3.11–3.13 on PATH (3.14 wheels don't build)." >&2
    exit 1
  fi
  "$PY" -m venv "$BACKEND/.venv"
  "$VENV_PY" -m pip install -U pip -q
  "$VENV_PY" -m pip install -r "$BACKEND/requirements.txt"
fi

# --- frontend deps ----------------------------------------------------------
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "[dev] Installing frontend deps…"
  (cd "$FRONTEND" && npm install)
fi

# --- launch + clean shutdown ------------------------------------------------
PIDS=()
cleanup() {
  printf '\n[dev] Shutting down…\n'
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

if [ "$WITH_MOCK" = "1" ]; then
  echo "[dev] Mock Phoenix ERP → http://localhost:9000"
  (cd "$BACKEND" && exec "$VENV_PY" -m uvicorn mocks.mock_erp:app --host 0.0.0.0 --port 9000) &
  PIDS+=($!)
fi

echo "[dev] Backend  → http://localhost:8000  (health: /health)"
(cd "$BACKEND" && exec "$VENV_PY" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &
PIDS+=($!)

echo "[dev] Frontend → http://localhost:5173"
(cd "$FRONTEND" && exec npm run dev) &
PIDS+=($!)

printf '\n[dev] Up. Open http://localhost:5173 — press Ctrl-C to stop.\n\n'
wait
