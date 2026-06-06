# Technology Stack

Generated: 2026-06-06

## Runtime

- Backend: Python 3.11+ / 3.12 local venv, FastAPI, Uvicorn, Pydantic Settings.
- Frontend: React 18, TypeScript 5.6, Vite 5, React Router 6.
- Terminal: `@xterm/xterm` plus `@xterm/addon-fit` in the browser; `asyncssh` PTY support in the backend.
- LLM: OpenRouter through the OpenAI-compatible LangChain `ChatOpenAI` adapter.
- ERP integration: Phoenix REST API over `httpx`.
- Deployment/dev: Docker Compose with separate backend and frontend services; `dev.sh` and `Makefile` wrap local development flows.

## Backend Dependencies

- `fastapi==0.115.6`
- `uvicorn[standard]==0.34.0`
- `pydantic-settings==2.7.1`
- `httpx==0.28.1`
- `asyncssh==2.18.0`
- `langchain-core==0.3.29`
- `langchain-openai==0.2.14`
- `websockets==14.1`
- `pytest==8.3.4`
- `pytest-asyncio==0.25.2`

## Frontend Dependencies

- Runtime: React, React DOM, React Router, React Markdown, Remark GFM, xterm.
- Build: Vite, TypeScript, `@vitejs/plugin-react`.
- Scripts: `npm run dev`, `npm run build`, `npm run preview`.
- There is no frontend test, lint, or dedicated typecheck script; direct type checking is available through `npx tsc --noEmit`.

## Entrypoints

- Backend app: `backend/app/main.py`
  - `GET /health`
  - includes REST router from `backend/app/api/routes.py`
  - includes WebSocket router from `backend/app/api/ws.py`
- Backend REST API: `backend/app/api/routes.py`
  - `GET /api/me`
  - `GET /api/tickets`
  - `GET /api/tickets/{ticket_id}`
  - `GET /api/tickets/{ticket_id}/customer-system`
  - `POST /api/runs`
  - `GET /api/runs/{run_id}`
  - `POST /api/runs/{run_id}/stop`
  - `POST /api/runs/{run_id}/activity`
- Backend WebSocket API: `backend/app/api/ws.py`
  - `/ws/runs/{run_id}`
- Offline Phoenix mock: `backend/app/mock/phoenix.py`
  - mirrors the Phoenix ticket/customer/activity/status contract for local dev and tests.
- Frontend app: `frontend/src/main.tsx` and `frontend/src/App.tsx`
  - `/`
  - `/tickets/:id`
  - `/runs/:runId`

## Commands

- Install all local dependencies: `make install`
- Local backend/frontend against real Phoenix: `make dev`
- Local backend/frontend plus mock Phoenix: `make mock`
- Backend tests: `make test`
- Docker stack: `docker compose up --build`
- Backend manual run: `cd backend && .venv/bin/uvicorn app.main:app --reload`
- Frontend manual run: `cd frontend && npm run dev`

## Configuration And Secrets

- `.env.example` documents Phoenix, SSH, OpenRouter, and frontend API variables.
- `.env`, `.env.bak`, SSH keys, venvs, audit logs, pycache, node modules, and frontend build outputs are ignored by Git.
- `Settings` reads environment variables and `.env`/`../.env`, then keeps Phoenix tokens, OpenRouter keys, and SSH key paths on the backend.
- `keys/.gitkeep` is tracked so key directories exist without committing key material.

## Generated Architecture Map

- `.planning/codebase/MAP.json`: machine-readable architecture map.
- `.planning/codebase/MAP.html`: interactive architecture visualization.
- Current graph pass indexed 130 modules, 9,300 LOC, and 36 import/dataflow edges.
