# Conventions

Generated: 2026-06-06

## Python Backend

- Modules use docstrings to state each module's responsibility.
- Async code is used for network, SSH, WebSocket, and agent flow operations.
- Pydantic models in `app.models` and `app.agent.schemas` are the shared contracts.
- Backend imports use absolute `app.*` paths, enabled by `backend/pytest.ini`.
- Errors from external services are wrapped at adapter boundaries:
  - `PhoenixError` for Phoenix HTTP failures.
  - `SSHError` for SSH connection/execution failures.
  - `LLMError` for model/JSON issues.
- FastAPI routes are thin translators around application services.
- The agent loop is phase-oriented and emits `RunPhase` changes as events.
- STOP is handled with an asyncio event and must unblock pending approvals,
  hypothesis waits, and activity submission waits.

## Safety And Audit

- Any command that can affect a customer VM should flow through `execute_command()`.
- `decide()` classifies each command as `ALLOW`, `CONFIRM`, or `DENY`.
- DENY decisions are hard blocks and cannot be overridden by edited commands.
- Safe reads can auto-run only when `auto_approve_reads` is enabled for the run.
- Mutations require technician approval.
- Command output is redacted before logs, UI, LLM context, or activity text consume it.
- Audit records are append-only JSONL entries under the configured audit directory.

## Frontend

- The frontend uses functional React components with hooks.
- API types are centralized in `frontend/src/api/client.ts`.
- WebSocket events are intentionally loose through `RunEvent` because server events
  carry variant-specific payloads.
- The workspace treats backend events as source of truth for run state.
- StrictMode is intentionally omitted in `main.tsx` to avoid Vite dev double-opening
  run WebSockets.
- Styles are global CSS in `theme.css`/`index.css`, not component-scoped CSS.

## Testing

- Backend tests live centrally under `backend/tests/test_*.py`.
- `pytest-asyncio` is configured with `asyncio_mode = auto`.
- Tests prefer fakes for SSH, ERP, and LLM when exercising orchestration.
- The in-process Phoenix mock is used for ERP client behavior.
- Current tests emphasize safety, redaction, run coordination, agent loop behavior,
  and regression coverage for merged-branch adjustments.

## Local Operations

- `make install`, `make dev`, `make mock`, `make test`, and Docker Compose are the
  primary developer entry points.
- Runtime secrets and key material stay in `.env` and `keys/`, both ignored by Git.
- The mock Phoenix server is the default way to develop without real Builder Base
  credentials.
