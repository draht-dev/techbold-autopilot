# Conventions

Generated: 2026-06-07

## Python Backend

- Modules use descriptive docstrings that explain architectural responsibility.
- Async I/O is the default for routes, run lifecycle, ERP calls, SSH operations,
  and LLM calls.
- Pydantic models in `backend/app/models.py` define cross-boundary payloads.
- Settings are loaded through `pydantic-settings` in `backend/app/config.py`.
- FastAPI state (`app.state.erp`, `app.state.llm`, `app.state.manager`) wires
  infrastructure into routes.
- Run lifecycle state is represented by the `RunPhase` enum.
- Events use string names from `EventType` and are emitted through `Run.emit()`.
- Human gates use futures owned by `Run`: approvals, hypothesis selection,
  decisions, and activity submission.

## Safety And Command Execution

- Agent commands must use `execute_command()`; the agent loop should not call SSH
  directly.
- DENY decisions are hard-fails and cannot be overridden by a human.
- Mutating or unknown commands require confirmation.
- Safe reads can auto-run only when the run's `auto_approve_reads` setting allows
  it.
- Output is redacted before it is logged, streamed, shown to the LLM, or written
  into activity text.
- Agent-run commands are echoed into the terminal stream so there are no hidden
  command cells.

## Frontend

- The frontend is a typed React/Vite SPA using function components and hooks.
- `frontend/src/api/client.ts` centralizes REST calls, WebSocket URL construction,
  TypeScript DTOs, and final phase constants.
- Workspace state is driven by WebSocket events.
- Terminal UI uses xterm and sends raw PTY keystrokes/resizes over WebSocket.
- Ticket descriptions and resolution text render markdown through the dedicated
  Markdown component.

## Testing

- Backend tests live in `backend/tests/test_*.py`.
- Async tests rely on `pytest-asyncio` auto mode.
- External dependencies are faked for default tests: SSH, ERP, and LLM.
- FastAPI route tests use `TestClient`.
- Phoenix integration tests use `httpx.ASGITransport` against the in-process mock.
- No frontend test convention exists yet.

## Error Handling

- Phoenix API failures become `PhoenixError` and are translated to HTTP errors in
  routes.
- SSH connection errors become `SSHError` and move runs to `ERROR`.
- LLM failures become `LLMError` and move runs to `ERROR`.
- STOP raises `RunStopped` inside run waits and moves the run to `STOPPED`.
- Audit and resolution persistence failures are intentionally non-fatal to the
  active run.

## Secrets And Local Files

- `.env` and `keys/` are local runtime inputs and must stay out of source control.
- Private keys are mounted read-only into Docker at `/keys`.
- Audit output is redacted, but audit/resolution files are runtime artifacts.
- The graph generator can index local ignored file paths; do not treat generated
  path listings as proof that the file should be tracked.
