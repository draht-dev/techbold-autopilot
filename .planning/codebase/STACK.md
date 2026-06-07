# Technology Stack

Generated: 2026-06-07

## Product

AI Service Desk Autopilot: a technician-controlled incident workspace. It reads
Phoenix ERP tickets, connects to customer Linux systems over SSH, runs an
LLM-assisted diagnostic loop through a deterministic safety gate, and writes a
reviewed activity back to Phoenix.

## Runtime Components

### Backend

- Language: Python 3.11-3.13 recommended by `dev.sh`; local venv currently under
  `backend/.venv`.
- Framework: FastAPI with Uvicorn.
- Async/network dependencies: `httpx`, `asyncssh`, `websockets`.
- Config: `pydantic-settings`, with `.env` and environment variables.
- LLM integration: OpenRouter through OpenAI-compatible LangChain clients.
- Tests: `pytest` and `pytest-asyncio`.

Important backend paths:

- `backend/app/main.py` - FastAPI app and lifespan wiring.
- `backend/app/api/routes.py` - REST API for tickets and runs.
- `backend/app/api/ws.py` - WebSocket API for run events and technician input.
- `backend/app/models.py` - Pydantic contracts for Phoenix entities and run/event
  state.
- `backend/app/runs/manager.py` - run aggregate and run registry.
- `backend/app/agent/loop.py` - autonomous troubleshooting loop.
- `backend/app/agent/tools.py` - command execution choke point.
- `backend/app/safety/rules.py` - deterministic command classifier and redaction.
- `backend/app/erp/client.py` - Phoenix ERP client.
- `backend/app/ssh/runner.py` - SSH command runner and interactive PTY.
- `backend/app/audit/log.py` - append-only per-run JSONL audit log.
- `backend/app/mock/phoenix.py` - offline Phoenix ERP mock.

### Frontend

- Language: TypeScript with React 18.
- Build tool: Vite 5.
- Routing: `react-router-dom`.
- Markdown rendering: `react-markdown` and `remark-gfm`.
- Terminal: `@xterm/xterm` and `@xterm/addon-fit`.

Important frontend paths:

- `frontend/src/api/client.ts` - REST/WebSocket client and TypeScript contract
  mirror.
- `frontend/src/pages/TicketList.tsx` - ticket queue.
- `frontend/src/pages/TicketDetail.tsx` - ticket details, run start/resume, shell.
- `frontend/src/pages/Workspace.tsx` - live run workspace.
- `frontend/src/components/*` - terminal, hypotheses, approvals, decisions,
  activity review, agent stream, and resolution views.

## External Systems

- Phoenix ERP API: upstream ticket/customer/activity system, documented in
  `docs/phoenix-openapi.yaml`.
- Customer Linux VM: reached over SSH using private keys from `keys/`.
- OpenRouter: LLM gateway for the autonomous agent and activity drafting.

## Persistence

- Phoenix ERP is the source of truth for tickets and submitted activities.
- `backend/audit_logs/<run_id>.jsonl` stores per-run audit evidence.
- `backend/audit_logs/resolutions/<ticket_id>.json` stores ticket-keyed
  submitted resolution snapshots for display after a run is finished.
- Run state is otherwise in-memory and bounded by `max_retained_runs`.

## Entry Points

From `.planning/codebase/MAP.json`:

- `backend/app/main.py` - HTTP `/health` plus mounted backend API.
- `backend/app/api/routes.py` - REST `/api/*` run and ticket operations.
- `backend/app/mock/phoenix.py` - mock Phoenix HTTP API.
- `frontend/src/main.tsx` / `frontend/index.html` - browser application.
- `dev.sh`, `Makefile`, and `docker-compose.yml` - local and container runners.

## Build And Run

- Local install: `make install`.
- Local backend + frontend: `make dev` or `./dev.sh`.
- Local backend + frontend + mock Phoenix: `make mock` or `./dev.sh --mock`.
- Docker stack: `docker compose up --build` or `make build`.
- Backend tests: `make test`.
- Frontend production build/typecheck: `cd frontend && npm run build`.

## Tooling Gaps

- No CI workflow was found.
- No coverage tooling or threshold is configured.
- No lint script or backend type checker is configured.
- No frontend test framework is configured.
