# Domain Model

Generated: 2026-06-07

## Bounded Contexts

### Phoenix ERP Integration

Owns the adapter/anti-corruption boundary around the external Phoenix ERP contract:
employee profile, tickets, customers, customer systems, status updates, and activity
creation. The local mock implements the same contract for offline development and
tests.

Primary modules:

- `backend/app/models.py`
- `backend/app/erp/client.py`
- `backend/app/mock/phoenix.py`
- `docs/phoenix-openapi.yaml`

### Phoenix Mock

Owns the offline development/test substitute for the upstream Phoenix API: seed
tickets, customer systems, activity creation, status updates, reset behavior, and
auth simulation.

Primary modules:

- `backend/app/mock/phoenix.py`

Boundary note: this should remain a substitute for Phoenix, not a second source of
business rules.

### Incident Run Lifecycle

Owns one troubleshooting run per ticket: run registration, supersede behavior,
phase transitions, event history, replay, async human gates, STOP handling,
terminal PTY coordination, shutdown, retention, and resolution lookup.

Primary modules:

- `backend/app/runs/manager.py`
- `backend/app/runs/resolutions.py`
- `backend/app/api/routes.py`
- `backend/app/api/ws.py`

Boundary note: this is the central application context. It coordinates agent,
Phoenix, SSH, audit, and frontend event contracts.

### Agent Investigation

Owns the LLM-backed troubleshooting behavior: recon context, continuous tool-calling
session, context compaction, ranked hypotheses, technician decisions, proposed fixes,
validation, finishing, and activity drafting.

Primary modules:

- `backend/app/agent/loop.py`
- `backend/app/agent/activity.py`
- `backend/app/agent/prompts.py`
- `backend/app/agent/schemas.py`
- `backend/app/agent/llm.py`
- `backend/app/agent/session.py`
- `backend/app/agent/agent_tools.py`

### Command Safety, Execution, And Audit

Owns deterministic command classification, hard-deny rules, human approval
decisions, command execution gating, output redaction, terminal command echoing, and
append-only audit logging.

Primary modules:

- `backend/app/safety/rules.py`
- `backend/app/agent/tools.py`
- `backend/app/audit/log.py`

### Customer System Access

Owns SSH connections, command execution on customer VMs, command timeouts,
connection shutdown, and interactive PTY shell support.

Primary modules:

- `backend/app/ssh/runner.py`

### Technician Console

Owns browser view state for ticket browsing, customer system display, run controls,
approvals, terminal interaction, hypothesis review, comments, own hypotheses,
decisions, resolution display, and activity review.

Primary modules:

- `frontend/src/api/client.ts`
- `frontend/src/pages/TicketList.tsx`
- `frontend/src/pages/TicketDetail.tsx`
- `frontend/src/pages/Workspace.tsx`
- `frontend/src/components/*`

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
- Agent Message: streamed assistant/tool-result visibility for the technician
  workspace.
- Decision Request: a human judgement gate when the agent is blocked or uncertain.
- Event: a WebSocket message such as `run.state`, `approval.request`,
  `decision.request`, `agent.message`, `hypotheses`, `term.data`,
  `validation.result`, or `activity.draft`.
- Hypothesis: a candidate technical root cause with rank, evidence, checks,
  likelihood, source, comments, and status.
- Interactive PTY: the technician's live shell session on the customer VM.
- Phoenix ERP: upstream ticket/customer/activity API.
- Phoenix Mock: in-memory simulator of Phoenix with seed tickets, systems, activities,
  employee profile, and status updates.
- Recon: deterministic read-only evidence gathering before hypothesis generation.
- Remote Execution: SSH command execution and interactive shell support against a
  customer VM.
- Resolution: a ticket-keyed record of the submitted activity explaining how a
  finished ticket was fixed.
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

- Phoenix ERP is upstream of Phoenix ERP Integration. `PhoenixClient` is the
  anti-corruption layer that maps external JSON into Pydantic DTOs and writes
  activities/statuses back upstream.
- Phoenix ERP Integration supplies ticket and customer-system context to Incident
  Run Lifecycle.
- Phoenix Mock substitutes for the external Phoenix upstream in dev and tests; it
  should match Phoenix's API semantics rather than define new product behavior.
- Incident Run Lifecycle is the central application context. It coordinates Agent
  Investigation, Command Safety/Execution/Audit, Customer System Access, Phoenix
  status/activity writes, and Technician Console event contracts.
- Agent Investigation is downstream of Incident Run Lifecycle for run state,
  ticket context, approvals, decisions, event emission, and audit access.
- Command Safety, Execution, And Audit is upstream of Customer System Access for
  command execution. Agent-originated commands and legacy one-shot manual commands
  must pass through `execute_command`.
- Customer System Access is an infrastructure adapter and should not own incident
  decisions.
- Technician Console is downstream of backend REST/WebSocket APIs. Its
  `frontend/src/api/client.ts` file is a manual browser-side contract mirror.
- Phoenix Mock substitutes for the external Phoenix upstream in dev and tests.
- Shared kernel: Pydantic models in `backend/app/models.py` and TypeScript
  interfaces in `frontend/src/api/client.ts` represent the shared ticket/run/event
  vocabulary.
- Boundary concerns: `Run` is a large aggregate, `agent/loop.py` mutates `Run`
  directly, `api/routes.py` contains orchestration logic, and frontend/backend
  contracts can drift because schemas are manually mirrored. `agent/tools.py`
  contains command safety/execution infrastructure while living under the agent
  package.

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
- `AgentDecision`
- `Hypothesis`
- `HypothesisComment`
- `EventType`
- `RunEvent`
- `ActivityDraft`
- `ResolutionStore`

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
- `decision.request`
- `decision.resolved`
- `agent.message`
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
- `decision`
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
- `decision_request`
- `decision`
- `fix_approved`
- `validation`
- `activity_submitted`
- `shell_open`
