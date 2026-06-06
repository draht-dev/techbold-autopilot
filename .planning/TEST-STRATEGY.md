# Test Strategy

Generated: 2026-06-06 17:00:21 CEST

Source inputs: `draht-tools map-codebase .`, `draht-tools map-graph .`,
verifier subagent report, and direct inspection of manifests and tests.

## Test Framework

Backend:

- Framework: pytest
- Async support: pytest-asyncio
- Config: `backend/pytest.ini`
- Discovery: `testpaths = tests`, `python_files = test_*.py`
- Dependencies: `pytest==8.3.4`, `pytest-asyncio==0.25.0`, `httpx`, `anyio`

Frontend:

- No application test runner is configured.
- `npm run build` runs `tsc -b && vite build` and is the current automated frontend check.

Repository:

- No CI workflow was found.
- No Makefile or root-level test runner was found.
- No coverage configuration was found.

## Directory Conventions

- Backend tests are centralized under `backend/tests/`.
- Test files use `test_*.py`.
- Tests are not co-located with source files.
- There are no frontend `__tests__`, `test`, `e2e`, Playwright, Cypress, Jest, or Vitest application tests.

## Coverage Goals

Current explicit coverage goals: none.

Practical baseline:

- Backend pytest suite should pass before merging backend changes.
- Frontend `npm run build` should pass before merging frontend changes.
- Safety and redaction changes require targeted tests because those modules are high-blast-radius policy code.

Recommended coverage evolution:

- Add `pytest-cov` for backend coverage reporting.
- Start with a ratcheting threshold instead of a high fixed threshold.
- Keep stricter expectations for `app/safety/*`, `audit_log.py`, and `activity_generator.py`.
- Add frontend component and browser coverage before relying on UI changes.

## Testing Levels

### Backend Unit Tests

Existing coverage includes:

- Safety classifier: `backend/tests/test_safety_classifier.py`
- Redaction and logging filter: `backend/tests/test_redaction.py`
- Activity generation: `backend/tests/test_activity_generator.py`
- LLM provider selection/configuration: `backend/tests/test_llm_provider.py`
- SSH key path resolution: `backend/tests/test_ssh_runner.py`

### Backend Offline Integration Tests

Existing coverage includes:

- Phoenix ERP client against in-process mock ERP with `httpx.ASGITransport`: `backend/tests/test_erp_client.py`
- Full agent loop using `StubLLM`, `MockSSHRunner`, and `FakeIO`: `backend/tests/test_agent_loop_mock.py`
- FastAPI session/approval/activity flow via ASGI transport in `test_agent_loop_mock.py`

### Frontend Verification

Existing automated check:

- TypeScript/Vite production build through `npm run build`

Missing:

- Component tests
- API client tests
- SSE event handling tests
- Accessibility checks
- Browser E2E workflows

### End-to-End / Live Integration

Not currently automated:

- Real Phoenix ERP
- Real SSH host
- Real LLM providers
- Browser-based technician workflow

## Excluded Or Missing

- Frontend test infrastructure
- Browser E2E for approval/edit/reject/retry/abort/activity flows
- CI enforcement
- Coverage thresholds and reports
- Test markers by level (`unit`, `integration`, `e2e`)
- Live provider contract tests
- Root-level one-command verification

## Recommended Commands

Full backend suite:

```bash
cd backend
.venv/bin/python -m pytest -q
```

Safety and redaction focus:

```bash
cd backend
.venv/bin/python -m pytest tests/test_safety_classifier.py tests/test_redaction.py -q
```

Backend integration focus:

```bash
cd backend
.venv/bin/python -m pytest tests/test_erp_client.py tests/test_agent_loop_mock.py -q
```

Frontend build:

```bash
cd frontend
npm run build
```

Future backend coverage command after adding `pytest-cov`:

```bash
cd backend
.venv/bin/python -m pytest --cov=app --cov-report=term-missing
```

## Recommendations

- Add CI that runs backend pytest and frontend build on every PR.
- Add `pytest-cov` with a ratcheting threshold.
- Add Vitest and React Testing Library for frontend component/API/SSE behavior.
- Add Playwright E2E against mock backend/ERP for ticket selection, approval, activity review, and submit flows.
- Add pytest markers for `unit`, `integration`, and future `e2e` groups.
- Add a root Makefile or scripts so contributors have one canonical verification command.
