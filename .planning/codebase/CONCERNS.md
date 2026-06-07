# Concerns

Generated: 2026-06-07

## Architectural Concerns

- `Run` is the dominant aggregate. It owns coordination, event history, terminal
  replay, SSH shell, audit, approvals, decisions, hypotheses, activity state, and
  ERP references. This is workable for the current scope, but future behavior may
  need smaller application services or explicit ports.
- `backend/app/models.py` is a broad shared kernel that mixes Phoenix ERP DTOs and
  internal run/event contracts.
- Frontend DTOs are manually mirrored in `frontend/src/api/client.ts`; there are no
  contract tests or generated client types to catch drift.
- `api/routes.py` starts agent and shell tasks directly, so route handlers contain
  orchestration logic.
- `api/ws.py` directly invokes `execute_command()` for legacy terminal input.
- `agent/loop.py` imports and mutates `Run` directly, so agent orchestration and
  lifecycle state are tightly coupled.
- `agent/tools.py` is safety/execution infrastructure located under the agent
  package, which blurs the package boundary.

## Testing Concerns

- Backend tests pass offline, but there is no CI workflow enforcing them.
- There is no coverage tool or threshold.
- Frontend has no behavioral tests; only TypeScript/Vite buildability has been
  checked.
- WebSocket protocol behavior is not directly tested: replay, inbound actions,
  disconnect cleanup, terminal data/resize, approvals, decisions, and legacy
  terminal commands.
- No contract tests prove backend Pydantic payloads, WebSocket events, Phoenix
  OpenAPI, and frontend TypeScript interfaces remain aligned.
- Live Phoenix, SSH, and OpenRouter behavior is intentionally excluded from the
  default suite; this is practical, but it leaves external integration risk.

## Security / Operations Concerns

- Local ignored files such as `.env` and key filenames can appear in generated
  graph artifacts if present in the workspace. Do not commit secret contents, and
  review generated maps before publishing outside the team.
- Host key checking is disabled for hackathon VMs.
- Interactive PTY input is a trusted human channel and does not pass through the
  per-command classifier; agent and legacy one-shot commands do.
- Audit persistence failure is non-fatal. Runs continue with in-memory audit
  entries, but durable evidence may be incomplete if the audit volume is
  unwritable.
- Resolution persistence failure is non-fatal. The current process may still show
  the solution, but restart/GC durability depends on the `audit_logs` volume.

## Documentation Concerns

- `README.md` says 81 tests pass offline, but current verifier evidence collected
  113 backend tests passing.
- `make test` runs backend pytest only; frontend build/typecheck is separate.
- Generated graph clusters treat the whole backend as one structural cluster, so
  `.planning/codebase/MAP.json` should be supplemented with this document and
  `.planning/DOMAIN.md` for semantic bounded contexts.

## Recommended Next Actions

1. Add `make verify` for backend pytest plus frontend build/typecheck.
2. Add CI for the selected verification gate.
3. Add backend WebSocket integration tests.
4. Add contract tests for REST and WebSocket payloads.
5. Add Vitest/React Testing Library for core frontend states.
6. Add one mock-backed Playwright smoke flow.
7. Add coverage tooling and initial thresholds.
