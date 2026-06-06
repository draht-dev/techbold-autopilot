# Architecture

Generated: 2026-06-06 17:00:21 CEST

## System Overview

`techbold` is a supervised AI service-desk autopilot for Linux incident tickets.
The frontend presents a technician workspace. The backend fetches assigned
Phoenix ERP tickets, starts one troubleshooting session per ticket, streams
agent progress over SSE, gates mutating SSH commands behind human approval,
redacts all sensitive output, persists an audit log, drafts an ERP activity,
and submits the reviewed activity back to Phoenix.

Core invariant: the LLM proposes actions, but it never executes commands
directly. Execution always passes through command classification, approval
gates, SSH runner hard-block rechecks, redaction, and audit logging.

## Architectural Layers

| Layer | Code | Responsibility |
|---|---|---|
| Presentation | `frontend/src/*`, `backend/app/routers/*` | Technician UI, REST facade, SSE session stream, approval endpoints. |
| Application | `backend/app/session.py`, `backend/app/agent/orchestrator.py` | Session lifecycle and deterministic troubleshooting workflow. |
| Domain policy | `backend/app/safety/*`, `backend/app/activity_generator.py`, `backend/app/audit_log.py` | Command safety, redaction, audit trail, activity drafting rules. |
| Infrastructure | `backend/app/erp_client.py`, `backend/app/ssh_runner.py`, `backend/app/agent/llm.py` | Phoenix ERP HTTP, SSH, and LLM provider adapters. |
| Shared contracts | `backend/app/models.py`, `frontend/src/types.ts`, `backend/app/agent/contracts.py` | Pydantic and TypeScript DTOs, LLM structured response contract. |
| Test support | `backend/mocks/*`, `backend/tests/*` | Mock ERP, mock SSH, fixture-driven offline tests. |

## Main Flows

### Ticket Browsing

1. `TicketList` calls `frontend/src/api.ts`.
2. Backend `routers/tickets.py` calls `ErpClient`.
3. `ErpClient` sends authenticated Phoenix ERP requests.
4. Backend returns typed `Ticket` and redacted `CustomerSystem` data to the UI.

### Troubleshooting Session

1. `TicketDetail` creates and starts a session through `routers/agent.py`.
2. `SessionManager` builds `ErpClient`, LLM client, SSH runner, `AuditLog`, and `Orchestrator`.
3. `Session` stores SSE history, pending approval futures, abort state, and the background agent task.
4. `Orchestrator` runs phases: `triage`, `diagnose`, `propose_fix`, `apply`, `validate`, `persist_check`, `document`.
5. `AgentWorkspace` receives live events over `EventSource`.

### Command Execution Gate

1. LLM returns `ProposedCommand` objects in a strict `AgentResponse`.
2. `Orchestrator` classifies each command with `classify()`.
3. `HARD_BLOCK` commands are rejected, audited, and fed back to the LLM.
4. Read-only commands can auto-run or wait for batch confirmation depending on settings.
5. Mutating commands emit `awaiting_approval` and wait for technician approve/edit/reject.
6. Edited commands are reclassified.
7. `SSHRunner` rechecks `HARD_BLOCK` before execution as defense in depth.
8. Output is redacted, truncated, stored in `AuditLog`, and emitted to the UI.

### Activity Submission

1. `activity_generator.build_activity()` derives the graded 8-field `ActivityCreate` from `AuditLog` and `FinalReport`.
2. The frontend renders the draft in `ActivityReview`.
3. Technician edits and submits.
4. `Orchestrator.submit()` calls `ErpClient.create_activity()` and then marks the ticket `DONE`.

## Bounded Contexts

See `.planning/DOMAIN.md` for the domain-oriented context map. In code, the
most visible contexts are:

- Phoenix ERP / Ticketing Integration
- Troubleshooting Session / Agent Orchestration
- Safety & Redaction Policy
- Remote Execution
- Audit & Activity Documentation
- LLM Provider / Agent Contract
- Technician Workspace UI

## Module Boundaries

| Module | Boundary Notes |
|---|---|
| `erp_client.py` | Anti-corruption layer for Phoenix ERP. Owns auth headers, timeout/retry policy, 404-to-None behavior, and typed errors. |
| `ssh_runner.py` | Remote execution adapter. Owns asyncssh connection reuse, key path resolution, command timeouts, reconnects, and hard-block refusal. |
| `safety/` | Shared policy kernel. Pure command classification plus redaction/logging filter. Imported by orchestrator, runner, mock runner, routers, audit, and activity generation. |
| `agent/orchestrator.py` | Application workflow root. Coordinates LLM, ERP, SSH, safety, audit, SSE, and activity drafting. |
| `session.py` | Runtime composition root and in-memory registry. Owns pending approval futures and SSE event history. |
| `audit_log.py` | Redacted append-only audit persistence. Intended source of truth for generated activity content. |
| `activity_generator.py` | Converts audit history and final report into ERP activity fields. |
| `frontend/src/api.ts` | Browser-facing backend client, including `EventSource` subscription setup. |

## Context Relationships

- Phoenix ERP is upstream of the backend. `ErpClient` is the ACL between ERP contracts and app code.
- The technician UI is downstream of backend REST/SSE contracts and does not receive ERP tokens or backend secrets.
- Troubleshooting Session is downstream of Phoenix ERP, LLM Provider, Safety, Remote Execution, and Audit.
- Safety is a shared kernel used at multiple boundaries.
- Remote Execution is downstream of Safety and upstream of Audit through `CommandResult`.
- Audit & Activity is downstream of Orchestrator and upstream of Phoenix ERP activity submission.

## Notable Coupling

- `backend/app/models.py` is a broad shared kernel containing ERP entities, agent contract models, execution/audit models, SSE events, and backend request bodies.
- `Orchestrator` directly imports most infrastructure adapters and policy modules, which keeps behavior explicit but makes it a high-change file.
- `SessionManager` is both object factory and session registry.
- Frontend TypeScript types manually mirror backend pydantic models and can drift.
- Mock ERP duplicates schema definitions instead of importing backend contracts.

## Living Map

The generated living map artifacts are:

- `.planning/codebase/MAP.json`
- `.planning/codebase/MAP.html`

Use `MAP.json` for module/file orientation before starting future work. Use
`.planning/DOMAIN.md` for conceptual boundaries.
