# Technology Stack

Generated: 2026-06-07

## Runtime Surfaces

- Backend: Python FastAPI service under `backend/`.
- Frontend: React/Vite/TypeScript app under `frontend/`.
- External systems:
  - Phoenix ERP REST API.
  - Customer Linux VMs over SSH.
  - OpenRouter/OpenAI-compatible chat API via LangChain.
- Local development substitute: in-process Phoenix mock at
  `backend/app/mock/phoenix.py`.

## Backend Stack

- Python: intended local range is Python 3.11 to 3.13; `dev.sh` prefers
  `python3.12`.
- Web framework: `fastapi==0.115.6`.
- ASGI server: `uvicorn[standard]==0.34.0`.
- Settings: `pydantic-settings==2.7.1`.
- HTTP client: `httpx==0.28.1`.
- SSH: `asyncssh==2.18.0`.
- LLM integration:
  - `langchain-core==0.3.29`
  - `langchain-openai==0.2.14`
  - OpenRouter base URL defaults to `https://openrouter.ai/api/v1`.
- WebSocket support: FastAPI/Starlette plus `websockets==14.1`.
- Tests:
  - `pytest==8.3.4`
  - `pytest-asyncio==0.25.2`

## Frontend Stack

- Node package manager: npm with `frontend/package-lock.json`.
- Build tool: `vite`.
- TypeScript: `typescript`.
- UI framework: React 18.
- Routing: `react-router-dom`.
- Markdown: `react-markdown` with `remark-gfm`.
- Terminal: `@xterm/xterm` plus `@xterm/addon-fit`.
- No frontend test framework is configured today.

## Build And Run

- Install all local dependencies:
  - `make install`
- Run backend and frontend against configured Phoenix:
  - `make dev`
  - `./dev.sh`
- Run backend, frontend, and mock Phoenix:
  - `make mock`
  - `./dev.sh --mock`
- Run backend tests:
  - `make test`
  - `cd backend && .venv/bin/python -m pytest -q`
- Build/run with Docker:
  - `docker compose up --build`

## Docker

- `backend/Dockerfile`: backend service image.
- `frontend/Dockerfile`: frontend service image.
- `docker-compose.yml` exposes:
  - Backend on `localhost:8000`.
  - Frontend on `localhost:5173`.
- Compose mounts:
  - `./keys:/keys:ro` for SSH keys.
  - `./backend/audit_logs:/app/audit_logs` for audit/resolution persistence.

## Configuration

Backend settings come from environment variables and `.env` files via
`SettingsConfigDict(env_file=(".env", "../.env"))`.

Important variables:

- `PHOENIX_API_BASE_URL`
- `PHOENIX_API_TOKEN`
- `SSH_PRIVATE_KEY_PATH`
- `SSH_USERNAME`
- `OPENROUTER_API_KEY`
- `OPENROUTER_BASE_URL`
- `AGENT_MODEL`
- `FAST_MODEL`
- `AGENT_CONTEXT_MAX_TOKENS`
- `AGENT_CONTEXT_COMPACT_THRESHOLD`
- `AGENT_CONTEXT_KEEP_RECENT_MESSAGES`
- `AGENT_MAX_ITERATIONS`
- `AGENT_REASONING_EFFORT`
- `AUTO_APPROVE_READS_DEFAULT`
- `VITE_API_BASE`

## Source Tree

Relevant tracked source and docs:

```text
.
├── README.md
├── Makefile
├── dev.sh
├── docker-compose.yml
├── docs/
│   ├── phoenix-openapi.yaml
│   └── scoring.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── models.py
│   │   ├── api/
│   │   ├── agent/
│   │   ├── audit/
│   │   ├── erp/
│   │   ├── mock/
│   │   ├── runs/
│   │   ├── safety/
│   │   └── ssh/
│   └── tests/
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── package-lock.json
    ├── tsconfig.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── api/
        ├── components/
        └── pages/
```

Local/generated artifacts observed but not part of the architectural source:

- `.env`, `.env.bak`
- `backend/.venv`
- `frontend/node_modules`
- `frontend/dist`
- `backend/.pytest_cache`
- Python `__pycache__` directories
- runtime audit logs under `backend/audit_logs` and `backend/data/audit`
