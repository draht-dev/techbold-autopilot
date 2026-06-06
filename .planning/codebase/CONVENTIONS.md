# Conventions

Generated: 2026-06-07

## Code Style

- Python backend modules use typed functions, Pydantic models, async FastAPI
  handlers, and small adapter modules by infrastructure boundary.
- Backend domain/event shapes are centralized in `backend/app/models.py`.
- Long-running run state is coordinated with explicit async primitives on `Run`
  instead of global queues.
- Commands that touch customer VMs must route through `app.agent.tools.execute_command`;
  direct SSH execution is reserved for the `SSHRunner` adapter.
- Frontend code uses React function components, local `useState`/`useEffect`
  state, and a typed API client in `frontend/src/api/client.ts`.
- Frontend routes are page-oriented: ticket list, ticket detail, and run workspace.

## Testing Patterns

- Backend tests live under `backend/tests/test_*.py`.
- Async tests rely on `pytest-asyncio` with `asyncio_mode = auto`.
- External services are faked for deterministic offline tests:
  - Phoenix via `backend/app/mock/phoenix.py` and `httpx.ASGITransport`.
  - SSH via fake `SSHRunner`/`CommandResult` classes.
  - LLM via scripted fake tool-call responses.
- Run lifecycle tests use `tmp_path` for audit/resolution persistence.
- FastAPI route tests use `TestClient` and monkeypatch the agent/shell loops so
  tests do not open real SSH sessions or call an LLM.
- Frontend has no configured test runner yet.

## Error Handling

- Phoenix adapter maps auth, not-found, validation, 5xx, timeout, and connection
  errors into `PhoenixError`; REST routes convert those to HTTP errors.
- SSH connect errors raise `SSHError`; command execution returns `CommandResult`
  with timeout/error text instead of throwing where possible.
- Agent loop catches `RunStopped`, `SSHError`, `LLMError`, and unexpected
  exceptions, then emits visible run states/errors and closes SSH/PTY resources.
- Audit and resolution persistence are best-effort: write failures are suppressed
  or logged so a live run is not crashed by local disk issues.
- STOP unblocks pending approvals/decisions/activity waits by setting the run's
  stop event.

## Contract Conventions

- Backend event names are defined in `EventType`.
- Frontend event and DTO names mirror backend names manually in
  `frontend/src/api/client.ts`.
- The Phoenix external API contract is documented in `docs/phoenix-openapi.yaml`
  and represented locally by Pydantic DTOs plus the mock ERP.
- Command output must be redacted before UI display, audit persistence, LLM
  visibility, or activity generation.
