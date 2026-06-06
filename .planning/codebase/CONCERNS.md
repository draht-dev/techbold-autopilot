# Concerns

Generated: 2026-06-06 17:00:21 CEST

## Boundary Concerns

- `backend/app/models.py` is an oversized shared kernel. It mixes ERP entities, agent contracts, execution/audit models, SSE events, and API request bodies. This is simple for a small app, but it raises drift and ownership risk as the system grows.
- `Orchestrator` directly imports ERP, SSH, safety, audit, activity generation, LLM contracts, settings, and shared models. It is clear and explicit, but it is the main coordination hotspot.
- `SessionManager` is both dependency factory and in-memory registry. Future production needs may require separating composition, persistence, and lifecycle supervision.
- Frontend types manually mirror backend contracts. There is no generated schema or shared OpenAPI/JSON-schema workflow, so frontend/backend DTO drift is possible.
- Mock ERP duplicates contract models. This keeps the mock standalone, but it can diverge from production backend expectations.

## Test Concerns

- No frontend automated tests are configured.
- No browser E2E coverage exists for the critical approval, edit, reject, abort, retry, activity review, and submit workflows.
- No coverage tool or threshold is configured.
- No CI workflow was found.
- Tests are not marked by level (`unit`, `integration`, `e2e`), so targeted verification is convention-based.
- Live integrations are intentionally not tested: real Phoenix ERP, real SSH hosts, and real LLM providers are outside the automated suite.

## Operational Concerns

- Runtime state is in memory except for audit JSONL files. This is fine for the current single-technician scope, but sessions will not survive process restart.
- `backend/data/audit` is intentionally git-ignored; production retention/rotation policy is not represented in code.
- Docker runs the frontend dev server rather than a production static asset build.
- There is no root-level Makefile or script for one-command verification across backend and frontend.

## Security Concerns

- The generated `MAP.json` and `MAP.html` include file paths from ignored areas such as `.env`, `keys/`, `node_modules`, `.venv`, `__pycache__`, and build artifacts. The generated map records paths/metadata, not file contents, but future publishing of planning artifacts should treat this as an exposure consideration.
- Command safety is central and well tested, but because it is a shared policy kernel, regressions in `classify()` or `redact()` have high blast radius.
- LLM provider configuration supports multiple vendors. Any new provider must preserve the same structured-output and redaction assumptions.

## Architectural Risks To Watch

- If more incident types or workflows are added, split `Orchestrator` by phase or introduce smaller application services before it becomes hard to test.
- If frontend development continues, establish generated or checked API contracts before manual TypeScript mirrors drift.
- If the app becomes multi-user or long-running, replace in-memory session state with durable session storage and explicit concurrency controls.
- If the audit log becomes compliance-relevant, add append integrity checks, retention controls, and review/export tooling.
