# Domain Model

Generated: 2026-06-06

## Bounded Contexts

### Service Desk / Phoenix Ticketing

Owns the technician-facing incident lifecycle: tickets, customer system data, run
creation, live troubleshooting, approvals, hypotheses, validation, activity review,
and ticket completion. It is also the downstream context for Phoenix ERP ticket,
customer, status, and activity contracts.

Primary modules:

- `backend/app/models.py`
- `backend/app/api/routes.py`
- `backend/app/erp/client.py`
- `docs/phoenix-openapi.yaml`
- `frontend/src/pages/TicketList.tsx`
- `frontend/src/pages/TicketDetail.tsx`

### Run Orchestration

Owns in-memory run state, phase transitions, event history, async waits, STOP
handling, subscriptions, hypothesis selection, terminal PTY coordination, and run
shutdown.

Primary modules:

- `backend/app/runs/manager.py`
- `backend/app/agent/loop.py`
- `backend/app/agent/activity.py`
- `backend/app/api/ws.py`
- `frontend/src/pages/Workspace.tsx`

### Diagnostic Agent

Owns LLM-backed diagnosis behavior: recon context, ranked root-cause hypotheses,
check verdicts, proposed fixes, validation interpretation, and activity drafting.

Primary modules:

- `backend/app/agent/prompts.py`
- `backend/app/agent/schemas.py`
- `backend/app/agent/llm.py`
- `backend/app/agent/tools.py`

### Safety And Audit

Owns deterministic command classification, approval decisions, hard-deny rules,
secret redaction, command/event audit logging, and the command execution choke point.

Primary modules:

- `backend/app/safety/rules.py`
- `backend/app/audit/log.py`
- `backend/app/agent/tools.py`

### Remote Execution

Owns SSH connections, command execution on customer VMs, command results, and the
interactive PTY shell.

Primary modules:

- `backend/app/ssh/runner.py`

### Technician Workspace UI

Owns browser view state for ticket browsing, customer system display, run controls,
approvals, terminal interaction, hypothesis review, comments, own hypotheses, and
activity review.

Primary modules:

- `frontend/src/api/client.ts`
- `frontend/src/pages/TicketList.tsx`
- `frontend/src/pages/TicketDetail.tsx`
- `frontend/src/pages/Workspace.tsx`
- `frontend/src/components/*`

### API / Composition

Owns application startup and composition of settings, Phoenix client, LLM client,
run manager, REST routes, and WebSocket routes. This is a facade/application boundary,
not a domain-owning context.

Primary modules:

- `backend/app/main.py`
- `backend/app/api/routes.py`
- `backend/app/api/ws.py`
- `backend/app/config.py`

### Phoenix Mock

Owns the in-memory simulator of the external Phoenix ERP contract for local
development and tests.

Primary modules:

- `backend/app/mock/phoenix.py`

### External Adapters

Owns adapters to Phoenix ERP, customer SSH hosts, OpenRouter, and the local Phoenix
mock used for development/testing.

Primary modules:

- `backend/app/erp/client.py`
- `backend/app/ssh/runner.py`
- `backend/app/agent/llm.py`
- `backend/app/mock/phoenix.py`
- `backend/app/config.py`

## Ubiquitous Language

- Activity: the ERP record written after a completed incident, containing summary,
  root cause, actions taken, command summary, validation result, and description.
- Activity Draft: generated activity fields reviewed or edited by the technician before
  submission.
- Approval: a human decision that permits connecting, running a command, or applying a
  fix; may include edited command text.
- Audit Log: append-only per-run JSONL evidence of phases, commands, comments,
  validation, and activity submission.
- Auto-Approve Reads: a run setting that lets known read-only commands execute without
  individual approval while mutations still require confirmation.
- Check: one or more read-only shell commands attached to a hypothesis to confirm or
  reject it.
- Customer System: the SSH host, port, user, OS, and notes associated with a ticket.
- DENY: safety decision for dangerous commands that must never run.
- Event: a WebSocket message such as `run.state`, `approval.request`, `hypotheses`,
  `term.data`, `validation.result`, or `activity.draft`.
- Hypothesis: a candidate technical root cause with rank, evidence, checks,
  likelihood, source, comments, and status.
- Interactive PTY: the technician's live shell session on the customer VM.
- Phoenix ERP: upstream ticket/customer/activity API.
- Phoenix Mock: in-memory simulator of Phoenix with seed tickets, systems, activities,
  employee profile, and status updates.
- Recon: deterministic read-only evidence gathering before hypothesis generation.
- Remote Execution: SSH command execution and interactive shell support against a
  customer VM.
- Run: one troubleshooting session for one ticket.
- Run Phase: lifecycle state such as `CONNECTING`, `RECON`, `HYPOTHESES`, `CHECK`,
  `APPLY`, `VALIDATE`, `PERSIST_VERIFY`, `ACTIVITY_DRAFT`, `DONE`, `STOPPED`, or
  `ERROR`.
- Safety Decision: deterministic classification of a command as `ALLOW`, `CONFIRM`, or
  `DENY`.
- STOP: technician abort signal that unblocks pending waits and closes the run.
- Ticket: Phoenix incident assigned to the team.
- Validation: proof command and LLM interpretation showing whether customer benefit is
  restored.

No app-owned database tables were found. Persistence is external Phoenix ERP plus
per-run audit JSONL. Mock persistence terms are `tickets`, `activities`, `_SYSTEMS`,
`_CUSTOMER_SYSTEMS`, and `activity_seq`.

## Context Map

- Phoenix ERP is upstream of Service Desk / Phoenix Ticketing. `PhoenixClient` is the
  anti-corruption layer that maps external JSON into Pydantic DTOs.
- Service Desk / Phoenix Ticketing is upstream of Run Orchestration for ticket and
  customer-system context, and downstream again when activities and `DONE` status are
  written back to Phoenix.
- Run Orchestration is upstream of Agent Diagnosis because it supplies run state,
  ticket context, approvals, event emission, and audit access.
- Diagnostic Agent is downstream of Safety And Audit for command execution because it
  cannot run customer commands directly.
- Safety And Audit is upstream of SSH execution and audit persistence; it protects both
  agent-originated commands and legacy one-shot terminal commands.
- Remote Execution is downstream of Run Orchestration and Safety And Audit; it should
  stay an infrastructure adapter.
- OpenRouter is upstream of Diagnostic Agent through the `LLM` adapter.
- Backend API / Composition is upstream of Technician Workspace UI; the frontend
  consumes REST and WebSocket contracts and mirrors backend DTO names.
- Phoenix Mock substitutes for the external Phoenix upstream in dev and tests.
- Shared kernel: Pydantic models in `backend/app/models.py` and TypeScript interfaces
  in `frontend/src/api/client.ts` represent the shared API/event vocabulary.

## Aggregates

### Ticket Aggregate

Root entity: `Ticket`

Related entities/value objects:

- `TicketStatus`
- `CustomerSystem`
- `SystemInfo`
- `Customer`
- `ActivityCreate`
- `Activity`

### Run Aggregate

Root entity: `Run`

Related entities/value objects:

- `RunManager`
- `RunPhase`
- `ApprovalDecision`
- `Hypothesis`
- `HypothesisComment`
- `EventType`
- `RunEvent`
- `ActivityDraft`

Boundary note: `Run` is currently a large aggregate. It owns coordination, event
history, SSH shell, audit log, approvals, hypotheses, activity state, and an ERP
reference.

### Command Execution Aggregate

Root entity: `ExecResult`

Related entities/value objects:

- `Decision`
- `Category`
- `CommandResult`
- `AuditLog`
- safety `ALLOW`/`CONFIRM`/`DENY`

### Diagnostic Agent Output Aggregate

Root entity: `HypothesesOutput`

Related entities/value objects:

- `HypothesisItem`
- `CheckOutput`
- `ProposedFix`
- `ValidationOutput`
- `ActivityOutput`
- `LLM`
- `LLMError`

`Hypothesis` is currently a child of `Run`, but conceptually could become its own
aggregate if diagnosis state grows beyond a single run.

### Remote Execution Aggregate

Root entity: `SSHRunner`

Related entities/value objects:

- `InteractiveShell`
- `CommandResult`
- `SSHError`

### Phoenix Mock Aggregate

Root entity: in-memory mock state in `backend/app/mock/phoenix.py`

Related entities/value objects:

- seed tickets
- systems keyed by ticket ID
- activities
- employee profile

## Domain Events

Existing event names are mostly WebSocket event types and audit record types.

WebSocket events:

- `run.state`
- `command.run`
- `hypotheses`
- `approval.request`
- `approval.resolved`
- `validation.result`
- `activity.draft`
- `activity.submitted`
- `term.data`
- `info`
- `error`

Inbound WebSocket commands:

- `select_hypothesis`
- `submit_hypothesis`
- `comment_hypothesis`
- `approval.decision`
- `mode.set`
- `submit_activity`
- `stop`
- `terminal.data`
- `terminal.resize`
- `terminal.input`

Audit event types observed in code:

- `phase`
- `command`
- `hypothesis_selected`
- `hypothesis_comment`
- `validation`
- `activity_submitted`
- `shell_open`
- `note`
