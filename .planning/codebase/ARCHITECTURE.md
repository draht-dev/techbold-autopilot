# Architecture

Generated: 2026-06-07

## System Shape

The repository is a two-service web app:

- `backend/`: FastAPI application that integrates Phoenix ERP, SSH, LLMs, safety
  rules, audit logging, and run lifecycle state.
- `frontend/`: React/Vite technician console that consumes backend REST and
  WebSocket APIs.

The central backend concept is a `Run`: one troubleshooting session for one
Phoenix ticket. A run owns phase state, event replay, approval/decision futures,
terminal replay, SSH connection, audit log, hypotheses, activity draft, and final
resolution metadata.

## Layer Map

### Presentation

- FastAPI REST routes in `backend/app/api/routes.py`.
- FastAPI WebSocket route in `backend/app/api/ws.py`.
- React pages and components under `frontend/src/pages` and
  `frontend/src/components`.
- Browser-side typed client in `frontend/src/api/client.ts`.

### Application

- `backend/app/runs/manager.py` coordinates run creation, superseding, STOP,
  active-run discovery, replay, retention, shutdown, and resolution lookup.
- `backend/app/agent/loop.py` coordinates the agent workflow: connect, recon,
  tool-calling, hypotheses, fix proposal, validation, activity drafting, and
  final status.
- `backend/app/agent/session.py` maintains continuous LLM conversation state and
  token-triggered compaction.

### Domain / Shared Contracts

- `backend/app/models.py` defines Phoenix entities, run phases, hypotheses, and
  WebSocket event names.
- `frontend/src/api/client.ts` manually mirrors the backend-facing DTOs for the
  browser.

### Infrastructure

- `backend/app/erp/client.py` adapts the external Phoenix ERP API.
- `backend/app/ssh/runner.py` adapts SSH command execution and interactive PTY.
- `backend/app/agent/llm.py` adapts OpenRouter/OpenAI-compatible chat models.
- `backend/app/audit/log.py` writes redacted JSONL audit entries.
- `backend/app/runs/resolutions.py` stores durable ticket resolution snapshots.
- `backend/app/mock/phoenix.py` provides an in-process Phoenix substitute for
  offline dev/tests.

### Safety And Execution

- `backend/app/safety/rules.py` classifies commands as `ALLOW`, `CONFIRM`, or
  `DENY` and redacts secrets.
- `backend/app/agent/tools.py` is the command execution choke point: safety
  classification, human approval, SSH execution, audit persistence, WebSocket
  command event, and terminal echo all happen there.

## Main Data Flow

1. Frontend lists tickets through `/api/tickets`; backend delegates to Phoenix.
2. Technician opens a ticket and starts an autonomous run with `POST /api/runs`.
3. Backend loads the ticket and customer system from Phoenix, creates a `Run`, and
   starts `run_agent()` as an async task.
4. The run asks for SSH connection approval over WebSocket.
5. After approval, SSH connects and deterministic read-only recon runs through
   `execute_command()`.
6. The LLM receives ticket context plus recon and drives tool calls.
7. Every agent command goes through `execute_command()`:
   safety decision -> optional approval -> SSH -> redaction -> audit -> event
   stream -> terminal echo.
8. The agent presents at least two ranked hypotheses and blocks for technician
   selection or a technician-authored hypothesis.
9. The agent proposes a fix as a reviewable plan, applies approved commands, and
   validates.
10. The activity draft is generated, reviewed, submitted to Phoenix, ticket status
    is updated, and the resolution snapshot is persisted.

## WebSocket Flow

`/ws/runs/{run_id}` replays semantic events plus bounded terminal scrollback, then
streams live events. Inbound messages drive technician actions:

- hypothesis selection, custom hypothesis, and comments
- approval and decision resolution
- auto-approve mode changes
- activity submission
- STOP
- interactive PTY input and resize
- legacy one-shot terminal command input

## Living Map

The graph artifacts are:

- `.planning/codebase/MAP.json`
- `.planning/codebase/MAP.html`

The graph currently indexes 141 modules, 11,625 LOC, 43 import edges, and three
HTTP entry points. It sees the frontend import graph more precisely than the
Python backend graph, so semantic backend boundaries should be read from source
and this document rather than only from graph clusters.

## Boundary Concerns

- `Run` is a large aggregate that owns lifecycle, events, human gates, SSH shell,
  audit, hypotheses, activity state, and ERP references.
- `backend/app/models.py` mixes Phoenix ERP DTOs with internal run/event
  contracts, making it a broad shared kernel.
- `frontend/src/api/client.ts` manually mirrors backend contracts; schema drift is
  possible.
- `backend/app/api/routes.py` starts agent tasks directly.
- `backend/app/api/ws.py` can invoke command execution for legacy terminal input.
- `backend/app/agent/loop.py` mutates `Run` directly and imports infrastructure
  concerns.
- `backend/app/agent/tools.py` contains safety/execution infrastructure but lives
  inside the agent package.
