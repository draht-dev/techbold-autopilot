# Technology Stack

Generated: 2026-06-06 17:00:21 CEST

This codebase is a two-service AI service-desk application:

- Backend API: FastAPI application that proxies Phoenix ERP data, manages troubleshooting sessions, gates SSH commands, stores audit logs, and drafts ERP activities.
- Frontend workspace: React/Vite technician UI for ticket selection, live SSE agent progress, approval controls, and activity review.
- Support apps: mock Phoenix ERP and mock SSH runner for offline development and tests.

## Runtime Stack

| Area | Stack | Main Files |
|---|---|---|
| Backend runtime | Python 3.11+, FastAPI, uvicorn, pydantic v2, pydantic-settings | `backend/app/main.py`, `backend/app/config.py`, `backend/requirements.txt` |
| Backend IO | `httpx` for Phoenix ERP and LLM HTTP, `asyncssh` for customer VM SSH, `sse-starlette` for server-sent events | `backend/app/erp_client.py`, `backend/app/ssh_runner.py`, `backend/app/routers/agent.py` |
| LLM adapters | Anthropic default, OpenAI, Azure OpenAI, plus `StubLLM` for tests | `backend/app/agent/llm.py` |
| Backend tests | pytest, pytest-asyncio, httpx ASGI transport | `backend/pytest.ini`, `backend/tests/` |
| Frontend runtime | React 18, TypeScript 5.6, Vite 5 | `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/package.json` |
| Deployment | Docker Compose, backend on `:8000`, frontend on `:5173`, SSH keys mounted read-only from `./keys` | `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile` |

## Package Manifests

The repository has no root package manager manifest. Tooling is split per app.

Backend dependencies come from `backend/requirements.txt`:

- `fastapi==0.115.6`
- `uvicorn[standard]==0.34.0`
- `pydantic==2.10.4`
- `pydantic-settings==2.7.1`
- `httpx==0.28.1`
- `asyncssh==2.18.0`
- `anthropic==0.42.0`
- `openai>=1.55,<2`
- `sse-starlette==2.1.3`
- `pytest==8.3.4`
- `pytest-asyncio==0.25.0`
- `anyio==4.7.0`

Frontend dependencies come from `frontend/package.json`:

- Runtime: `react`, `react-dom`
- Development: `typescript`, `vite`, `@vitejs/plugin-react`, React type packages
- Scripts: `npm run dev`, `npm run build`, `npm run preview`

## Entry Points

| Entry Point | Purpose |
|---|---|
| `backend/app/main.py` | Creates the FastAPI app, configures redacted logging, mounts routers, exposes `/health`. |
| `backend/app/routers/tickets.py` | Backend facade over Phoenix ERP ticket and customer-system reads. |
| `backend/app/routers/agent.py` | Session lifecycle, SSE stream, approval/reject/retry/abort, activity submit, dev reset. |
| `backend/mocks/mock_erp.py` | Standalone Phoenix-compatible mock ERP with the 8 required endpoints. |
| `frontend/src/main.tsx` | React application bootstrap. |
| `frontend/src/App.tsx` | Top-level view routing among ticket list, detail, and workspace. |

## Living Map Output

`draht-tools map-graph .` generated:

- `.planning/codebase/MAP.json`
- `.planning/codebase/MAP.html`

Observed graph statistics:

- 124 indexed modules
- 16,383 LOC
- 35 import/dataflow edges
- HTTP entry points detected in `backend/app/main.py`, `backend/app/routers/agent.py`, `backend/app/routers/tickets.py`, and `backend/mocks/mock_erp.py`
- Network sinks detected in `frontend/src/api.ts` and `backend/tests/test_agent_loop_mock.py`

The generated map is useful for file/module orientation. For conceptual bounded contexts, read `.planning/DOMAIN.md`; the graph currently groups mostly by broad containers rather than business contexts.

## Curated File Map

```text
backend/
  app/
    main.py                  FastAPI app and logging setup
    config.py                Environment-backed Settings and SSH connection resolution
    models.py                Shared pydantic contracts
    erp_client.py            Phoenix ERP HTTP ACL
    ssh_runner.py            Async SSH execution with hard-block recheck
    audit_log.py             Append-only redacted JSONL audit log
    activity_generator.py    ERP activity drafting from audit history and final report
    session.py               Session lifecycle and SessionManager composition root
    agent/
      orchestrator.py        Deterministic troubleshooting state machine
      llm.py                 LLM provider adapters
      doctrine.py            System prompt builder
      contracts.py           Structured LLM prompt/response helpers
    safety/
      classifier.py          Command classification API
      rules.py               Hard-block and read-only policy data
      redaction.py           Secret redaction and logging filter
    routers/
      tickets.py             Ticket/customer-system API
      agent.py               Agent session API and SSE
  tests/                     pytest suite
  mocks/                     mock ERP, mock SSH, sample fixtures

frontend/
  src/
    api.ts                   REST/SSE client helpers
    types.ts                 Frontend mirror of backend event and DTO types
    components/              Ticket, workspace, approval, activity review UI
```

## Standard Commands

Backend:

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm run build
npm run dev
```

Full app:

```bash
docker compose up --build
```
