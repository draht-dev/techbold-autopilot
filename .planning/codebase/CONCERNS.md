# Concerns

Generated: 2026-06-07

## Architecture

- `Run` is a large aggregate. It owns lifecycle state, event history, terminal
  replay, human gates, hypotheses, activity state, SSH shell, audit log, and ERP
  access. This is workable now, but future features should consider extracting
  smaller coordinators or ports.
- `agent/loop.py` imports and mutates `Run` directly, so agent behavior and run
  lifecycle are tightly coupled.
- `api/routes.py` has orchestration responsibilities: it fetches ERP data,
  creates runs, and starts agent/shell tasks. A service layer could make route
  handlers thinner if the API grows.
- `models.py` mixes Phoenix DTOs with internal run/event DTOs. Splitting external
  contracts from internal contracts would reduce shared-kernel blast radius.
- Frontend TypeScript interfaces manually duplicate backend Pydantic models.
  Contract drift is a real risk.

## Testing Gaps

- Backend pytest coverage is broad, but no coverage tool or threshold is
  configured.
- `api/ws.py` WebSocket replay/inbound-message behavior is not directly tested.
- Frontend has no unit/component tests and no browser end-to-end tests.
- No contract tests validate REST/WebSocket payload alignment between backend
  models and frontend `src/api/client.ts`.
- Real Phoenix, SSH, and OpenRouter paths are intentionally excluded from default
  tests; this is correct for offline reliability but leaves live integration risk.

## Security And Safety

- The safety boundary is deterministic and well tested, but the interactive PTY
  is intentionally a trusted technician channel and does not classify raw human
  keystrokes command-by-command.
- Audit and resolution files are local filesystem persistence; deployment must
  ensure the audit directory is mounted and protected.
- `.env`, `.env.bak`, generated audit logs, virtualenvs, frontend `dist`, and
  other local runtime artifacts exist in the worktree. Keep them excluded from
  commits and secret scans.

## Operational

- Run state is in memory. Active runs do not survive backend process restarts;
  only submitted resolution activity persists locally.
- `make test` runs backend pytest only. Frontend type checking and any future
  browser tests need an explicit verification command.
- No CI workflow is present to enforce backend tests, frontend type checking,
  coverage, or contract checks.
