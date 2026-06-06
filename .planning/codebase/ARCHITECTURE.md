# Architecture

Generated: 2026-06-06

## System Shape

This is a small full-stack service desk autopilot. The browser presents tickets,
approval gates, a live terminal, ranked hypotheses, and the final activity draft.
The backend owns all secrets, Phoenix ERP access, SSH access, agent orchestration,
deterministic safety checks, audit persistence, and LLM calls.

The core runtime flow is:

1. Frontend loads tickets and customer system information from backend REST routes.
2. `POST /api/runs` creates an in-memory `Run`, attaches Phoenix ticket/system data,
   and starts `run_agent()` as an asyncio task.
3. The workspace opens `/ws/runs/{run_id}`. The backend replays event history, then
   streams run state, terminal output, approvals, hypotheses, validation, and activity
   events.
4. The agent requests SSH connection approval, performs read-only recon, asks the LLM
   for ranked hypotheses, waits for technician selection or a technician-authored
   hypothesis, checks evidence, proposes a fix, validates it, verifies persistence,
   drafts an activity, and submits the approved activity to Phoenix.
5. Every agent or legacy manual command goes through `execute_command()`, which applies
   safety classification, approval, redaction, audit logging, SSH execution, and UI
   streaming.

## Backend Layers

- Presentation/API: `backend/app/main.py`, `backend/app/api/routes.py`,
  `backend/app/api/ws.py`.
  These files translate HTTP/WebSocket requests into run-manager, ERP, and agent
  operations. They should stay thin and avoid embedding diagnostic policy.
- Application orchestration: `backend/app/runs/manager.py`,
  `backend/app/agent/loop.py`, `backend/app/agent/activity.py`.
  This layer owns the human-in-the-loop run lifecycle, phase transitions, async waits,
  STOP handling, hypothesis/fix/validation flow, and activity drafting.
- Domain contracts: `backend/app/models.py`, `backend/app/agent/schemas.py`.
  These Pydantic models define Phoenix entities, internal run events, hypotheses,
  LLM structured outputs, and activity payloads.
- Safety and audit: `backend/app/safety/rules.py`, `backend/app/audit/log.py`,
  `backend/app/agent/tools.py`.
  Safety is deterministic and deny-first; audit redacts before writing JSONL.
- Infrastructure adapters: `backend/app/erp/client.py`, `backend/app/ssh/runner.py`,
  `backend/app/agent/llm.py`, `backend/app/config.py`.
  These modules wrap external systems: Phoenix HTTP, SSH/PTY, OpenRouter/LangChain,
  and environment configuration.
- Development fixture: `backend/app/mock/phoenix.py`.
  This mock mirrors the ERP contract for offline development and integration tests.

## Frontend Layers

- App shell/routing: `frontend/src/App.tsx`, `frontend/src/main.tsx`.
- API contracts and transport: `frontend/src/api/client.ts`.
- Pages: `TicketList`, `TicketDetail`, `Workspace`.
- Components: terminal, approvals, run controls, hypothesis list, activity review,
  markdown rendering.

The frontend keeps state local to pages/components. Backend events are treated as the
source of truth for run progress; the workspace maintains derived UI state from
WebSocket events.

## Data Flow

- Ticket browsing:
  `TicketList`/`TicketDetail` -> `api.client` -> backend `/api/tickets*` -> Phoenix client
  -> Phoenix ERP/mock.
- Run creation:
  `TicketDetail.start()` -> `POST /api/runs` -> Phoenix ticket/system fetch ->
  `RunManager.create_run()` -> background `run_agent()`.
- Live run:
  `Workspace` -> WebSocket -> `Run.emit()` event stream. Inbound messages resolve
  approvals, select/comment/submit hypotheses, toggle auto-approve reads, submit
  activity drafts, stop runs, and write terminal input.
- Command execution:
  `run_agent()` or legacy terminal command -> `execute_command()` -> `decide()` ->
  approval if needed -> `SSHRunner.run_command()` -> `redact()` -> `AuditLog.record()`
  -> event stream.
- Interactive terminal:
  Browser xterm keystrokes -> WebSocket `terminal.data` -> `Run.terminal_write()` ->
  `SSHRunner.open_shell()`/PTY -> terminal output mirrored as `term.data`.
- Activity submission:
  `draft_activity()` -> WebSocket `activity.draft` -> technician edit -> activity
  submission -> Phoenix `POST /api/v1/activities/create` -> ticket status `DONE`.

## Architectural Boundaries

- The LLM is not a safety boundary. Prompts carry safety instructions, but command
  permission is enforced by `backend/app/safety/rules.py`.
- Browser clients never receive Phoenix tokens, OpenRouter keys, or SSH keys.
- The agent loop never calls SSH directly for command execution; it uses
  `execute_command()` as the command choke point. The one exception is the explicit
  technician interactive PTY, which is treated as human input rather than agent action.
- Runs and event histories are in memory. There is no persistent run database.
- Audit JSONL is append-only per run and is the source material for activity drafting.
- `backend/app/models.py` is the broadest shared kernel: it contains Phoenix DTOs,
  internal run contracts, hypotheses, approvals, and event names.
- `frontend/src/api/client.ts` manually mirrors backend contracts; future schema changes
  should update both sides or introduce generated contracts.

## Generated Map

- `.planning/codebase/MAP.json` is the machine-readable map for future agents.
- `.planning/codebase/MAP.html` is the interactive layered visualization.
- The current map identifies HTTP entry points in the backend app, REST routes,
  and Phoenix mock, plus frontend network sinks in `Workspace.tsx` and
  `api/client.ts`.
