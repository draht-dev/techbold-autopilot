# Conventions

Generated: 2026-06-06 17:00:21 CEST

## Backend Code

- Python modules use typed pydantic v2 models for external and internal contracts.
- Async IO is the default for ERP, SSH, LLM, session control, and FastAPI routes.
- Infrastructure adapters expose typed errors rather than leaking raw library exceptions into routers.
- Routers translate domain/infrastructure failures into HTTP responses and redact error text before returning it.
- Command safety is centralized in `app.safety.classifier.classify`; command execution paths must not bypass it.
- Redaction is applied before data crosses persistence, UI, prompt, activity, and logging boundaries.
- `Orchestrator` is deterministic around state transitions; LLM output is structured data, not executable control flow.
- Test doubles implement the same informal protocols as production adapters (`MockSSHRunner`, `StubLLM`, `FakeIO`).

## Backend Contracts

- `backend/app/models.py` is the shared pydantic contract file.
- ERP-facing models use `extra="ignore"` so unexpected Phoenix fields do not crash parsing.
- `TicketStatus`, `Phase`, `Approval`, and `EventType` are string enums.
- `ActivityCreate` contains the graded ERP activity fields and defaults optional strings to empty values.
- `backend/app/agent/contracts.py` builds prompt payloads from redacted audit history and validates/parses model responses.

## Error Handling

- `ErpAuthError` is raised for Phoenix 401 with a clear token message.
- Phoenix 404s are converted to `None` by specific `ErpClient` methods.
- 5xx/network ERP failures retry up to 3 attempts with exponential backoff.
- `SSHRunner` refuses hard-blocked commands, clamps command timeouts, and retries once after connection loss.
- Session control tolerates duplicate or unknown approval resolution attempts.
- Public API error responses should redact exception text before returning it.

## Security And Safety

- Real secrets are expected in `.env` and `keys/`; both are ignored by git.
- Docker mounts `./keys` read-only into the backend container.
- The backend never exposes Phoenix tokens, LLM keys, or SSH keys to the frontend.
- Command output should be redacted before audit persistence, UI display, LLM feedback, and app logs.
- `HARD_BLOCK` commands are refused by both the orchestrator and SSH runner.

## Frontend Code

- `frontend/src/api.ts` owns all backend REST calls and SSE subscription setup.
- `frontend/src/types.ts` manually mirrors backend DTOs and event payloads.
- Components are function components with local React state.
- `App.tsx` owns coarse view state: ticket list, ticket detail, and active workspace.
- `AgentWorkspace` owns session event state, pending approval state, draft activity state, and command log rendering.

## Testing

- Backend tests live in `backend/tests`.
- pytest config is centralized in `backend/pytest.ini`.
- Test file convention is `test_*.py`.
- Async tests rely on `pytest-asyncio` with `asyncio_mode = auto`.
- Existing tests mix unit-style safety/redaction checks with offline integration tests using mock ERP/SSH/LLM.
- There is no configured frontend test runner; `npm run build` is the current frontend verification command.

## Development Commands

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

Docker:

```bash
docker compose up --build
```
