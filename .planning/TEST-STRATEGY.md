# Test Strategy

Generated: 2026-06-07

## Test Framework

- Backend: `pytest==8.3.4` with `pytest-asyncio==0.25.2`.
- Backend config: `backend/pytest.ini`
  - `asyncio_mode = auto`
  - `asyncio_default_fixture_loop_scope = function`
  - `pythonpath = .`
  - `testpaths = tests`
- Backend test utilities in use:
  - FastAPI `TestClient`
  - `httpx.ASGITransport`
  - `tmp_path`
  - `monkeypatch`
  - fake SSH, ERP, and LLM classes
- Frontend: no dedicated test framework is configured.
- Type checking: TypeScript strict mode is enabled. Direct check:
  `cd frontend && npx tsc --noEmit --pretty false`.
- CI: no `.github` workflow or equivalent automated gate is present.

## Directory Conventions

- Backend tests are centralized under `backend/tests/test_*.py`.
- Active source tests are 10 `test_*.py` files; the generated map also counts
  `backend/tests/__init__.py` as a test module.
- Backend tests are not co-located with source files.
- No frontend `*.test.tsx`, `__tests__/`, `e2e/`, Playwright config, or Cypress
  config exists.

## Coverage Goals

No coverage tooling or threshold exists today. There is no `pytest-cov`,
`.coveragerc`, frontend coverage provider, or package-level threshold.

Recommended starting goals:

- Backend global branch coverage: 80%.
- Safety and redaction modules: 95%+ branch coverage because they enforce
  hard-fail requirements.
- Run lifecycle and agent loop: 85%+ branch coverage for STOP, approval,
  rejection, validation failure, LLM unavailable, and SSH/API error paths.
- Frontend: add targeted component/workflow coverage before setting a percentage
  threshold.

## Testing Levels

### Unit

Covered:

- Safety hard-deny/confirm/allow classification and redaction:
  `backend/tests/test_safety.py`.
- SSH key selection by ticket:
  `backend/tests/test_config.py`.
- LLM JSON extraction fallback:
  `backend/tests/test_llm.py`.
- Agent session compaction:
  `backend/tests/test_session.py`.
- Likelihood normalization and PTY primitives:
  `backend/tests/test_adjustments.py`.
- Run manager approvals, STOP unblocking, decisions, hypothesis selection,
  comments, subscriptions, and event history:
  `backend/tests/test_run_manager.py`.
- Run lifecycle, concurrent run creation, supersede behavior, GC, and resolution
  persistence:
  `backend/tests/test_run_lifecycle.py`.

### Integration

Covered:

- Phoenix client against the in-process mock ERP:
  `backend/tests/test_erp_client.py`.
- FastAPI REST route wiring and run discovery:
  `backend/tests/test_routes.py`.
- Autonomous agent loop with fake SSH, ERP, and LLM:
  `backend/tests/test_agent_loop.py` and `backend/tests/test_adjustments.py`.

Missing:

- Direct WebSocket protocol tests for `backend/app/api/ws.py`: replay, inbound
  actions, disconnect cleanup, terminal resize/data, decision handling, and legacy
  terminal command path.
- Contract tests proving frontend TypeScript DTOs stay aligned with backend
  Pydantic responses/events and `docs/phoenix-openapi.yaml`.
- Direct edge-case tests for `agent/activity.py`, `agent/tools.py`, `audit/log.py`,
  `main.py` lifespan behavior, and real `SSHRunner` timeout/error behavior.

### Service-Level / Walking Skeleton

Covered:

- Full happy-path autonomous run with fakes: connect, recon, command execution,
  hypotheses, selected hypothesis, proposed fix, validation, activity submission,
  and `DONE`.
- Not-reproducible path that pauses for a technician decision and returns the
  ticket to `PENDING`.
- Multi-check hypotheses, technician-authored hypotheses, visible command echoing,
  and interactive terminal wiring.

Missing:

- Failure-path agent loop tests for rejected fixes, blocked fix commands, failed
  validation, LLM errors, SSH errors, Phoenix errors during finalize, and STOP at
  each major phase.

### Frontend

Currently missing:

- API client error handling tests.
- Ticket list/detail component tests.
- Approval and decision prompt tests.
- Hypothesis list tests for comments, own hypothesis, active/selection modes, and
  likelihood display.
- Workspace WebSocket state transition tests.
- Terminal resize/input/render behavior tests.
- Resolution display tests.

Recommended tools:

- Vitest plus React Testing Library for component and hook-level coverage.
- Playwright for the browser workflow once the mock-backed app can run reliably
  in test mode.

### End-To-End

Currently missing:

- Browser e2e covering ticket list -> ticket detail -> start run -> approve SSH ->
  select/check hypothesis -> approve fix -> review/submit activity -> DONE.
- WebSocket reconnection/history replay scenario.
- STOP during pending approval or active phase.

## Verification Commands

Evidence from the verifier subagent:

- `cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider`
  - Result: `113 passed in 1.14s`

Local commands to keep:

- `make test`
  - Runs backend pytest only.
- `cd frontend && npx tsc --noEmit --pretty false`
  - Runs frontend TypeScript checking.

## Excluded

- Live Phoenix ERP tests are excluded from the default suite because they require
  Builder Base credentials.
- Live SSH tests against customer VMs are excluded from the default suite because
  they require private keys and mutable external hosts.
- Live OpenRouter/LLM tests are excluded from the default suite because they
  require an API key and have nondeterministic/provider-dependent responses.
- Frontend production builds are not part of `make test`; `npm run build` writes
  `frontend/dist`.
- Runtime audit logs and local secrets are intentionally excluded from source
  control.

## Recommended Next Gates

1. Add `make verify` to run backend pytest and frontend TypeScript checking.
2. Add backend WebSocket integration tests with in-process fakes.
3. Add frontend Vitest/React Testing Library coverage for core UI states.
4. Add contract tests for REST and WebSocket payloads.
5. Add one Playwright smoke path for the demo-critical workflow.
6. Add coverage tooling and initial thresholds.
7. Add CI to enforce the selected gates on pull requests.
