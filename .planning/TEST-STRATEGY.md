# Test Strategy

Generated: 2026-06-06

## Test Framework

- Backend: pytest 8.3.4 with pytest-asyncio 0.25.2.
- Backend config: `backend/pytest.ini`
  - `asyncio_mode = auto`
  - `asyncio_default_fixture_loop_scope = function`
  - `pythonpath = .`
  - `testpaths = tests`
- Frontend: no dedicated test framework is currently configured.
- Type checking: TypeScript strict mode is enabled; direct check is
  `cd frontend && npx tsc --noEmit --pretty false`.
- CI: no `.github` workflow or equivalent CI gate is present.

## Directory Conventions

- Backend tests are centralized under `backend/tests/test_*.py`.
- Backend tests use fakes for SSH, ERP, and LLM when exercising orchestration.
- Phoenix client tests use the in-process FastAPI mock from `backend/app/mock/phoenix.py`.
- There are no co-located backend tests.
- There are no frontend tests under `frontend/src`.

## Coverage Goals

No coverage tooling or threshold exists today.

Recommended starting goals:

- Backend global branch coverage: 80%.
- Safety and redaction modules: 95%+ branch coverage because they enforce hard-fail
  requirements.
- Run orchestration and agent loop: 85%+ branch coverage for STOP, approval,
  rejection, validation failure, LLM unavailable, and SSH/API error paths.
- Frontend component and workflow coverage: add targeted tests before setting a
  percentage threshold.

## Testing Levels

### Unit

Covered:

- Safety hard-deny/confirm/allow classification and redaction:
  `backend/tests/test_safety.py`.
- SSH key selection by ticket:
  `backend/tests/test_config.py`.
- LLM JSON extraction fallback:
  `backend/tests/test_llm.py`.
- Likelihood normalization and PTY primitives:
  `backend/tests/test_adjustments.py`.

### Integration

Covered:

- Phoenix client against the in-process mock:
  `backend/tests/test_erp_client.py`.
- Run manager approval, STOP, hypothesis selection, comments, event history, and
  subscriptions:
  `backend/tests/test_run_manager.py`.

Missing:

- FastAPI route tests for `backend/app/api/routes.py`.
- WebSocket protocol tests for `backend/app/api/ws.py`.
- Activity submission/status update error-path tests.

### Service-Level / Walking Skeleton

Covered:

- End-to-end agent loop with fake SSH, ERP, and LLM:
  `backend/tests/test_agent_loop.py`.
- Regression paths for multi-check hypotheses, technician-authored hypotheses,
  visible command echoing, and interactive terminal wiring:
  `backend/tests/test_adjustments.py`.

Missing:

- Failure-path agent loop tests for rejected fixes, blocked commands, failed
  validation, LLM errors, SSH errors, and STOP at each major phase.

### Frontend

Currently missing:

- API client error handling tests.
- Ticket list/detail component tests.
- Approval prompt tests.
- Hypothesis list tests for comments, own hypothesis, active/selection modes.
- Workspace WebSocket state transition tests.
- Terminal resize/input behavior tests.

Recommended tools:

- Vitest plus React Testing Library for component and hook-level coverage.
- Playwright for the browser workflow.

### End-To-End

Currently missing:

- Browser e2e covering ticket list -> ticket detail -> start run -> approve SSH ->
  select/check hypothesis -> approve fix -> review/submit activity -> DONE.
- WebSocket reconnection/history replay scenario.
- STOP during pending approval or active phase.

## Verification Commands

Current evidence from the verifier subagent:

- `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`
  - Result: `81 passed in 1.32s`
- `cd frontend && npx tsc --noEmit --pretty false`
  - Result: exit code 0, no diagnostics

Primary local command:

- `make test`
  - Runs backend pytest only.

## Excluded

- Live Phoenix ERP tests are excluded from the default suite because they require
  Builder Base credentials.
- Live SSH tests against customer VMs are excluded from the default suite because they
  require private keys and mutable external hosts.
- Live OpenRouter/LLM tests are excluded from the default suite because they require an
  API key and have nondeterministic/provider-dependent responses.
- Frontend production builds are not part of `make test`; `npm run build` writes
  `frontend/dist`.
- Runtime audit logs and local secrets are intentionally ignored by Git.

## Recommended Next Gates

1. Add `make typecheck` or `make verify` to run backend tests plus frontend TypeScript.
2. Add backend route and WebSocket tests with in-process fakes.
3. Add frontend Vitest/React Testing Library coverage for core UI states.
4. Add Playwright e2e for the demo-critical run workflow.
5. Add coverage tooling and thresholds.
6. Add CI to enforce the selected gates on pull requests.
