# Domain Model

Generated: 2026-06-06 17:00:21 CEST

Source inputs: `draht-tools map-codebase .`, `draht-tools map-graph .`,
architect subagent report, and direct code inspection.

## Bounded Contexts

### Phoenix ERP / Ticketing Integration

External upstream ticketing system plus the backend anti-corruption layer.
Owns technician identity, assigned tickets, customers, customer systems,
ticket status updates, and activity creation.

Primary code:

- `backend/app/erp_client.py`
- `backend/app/routers/tickets.py`
- `backend/app/models.py`
- `backend/mocks/mock_erp.py`

### Troubleshooting Session / Agent Orchestration

Core workflow for resolving one ticket. Drives phases from triage through
diagnosis, fix proposal, approval-gated application, validation, persistence
check, documentation, and submit.

Primary code:

- `backend/app/session.py`
- `backend/app/agent/orchestrator.py`
- `backend/app/agent/contracts.py`

### Safety & Redaction Policy

Shared command safety and secret handling context. Classifies command text into
read-only, approval-required, or hard-blocked verdicts, and redacts secrets at
storage/display/prompt/log boundaries.

Primary code:

- `backend/app/safety/classifier.py`
- `backend/app/safety/rules.py`
- `backend/app/safety/redaction.py`

### Remote Execution

SSH execution adapter for customer Linux systems. Resolves key paths, opens and
reuses SSH connections, clamps timeouts, retries after connection loss, and
rechecks hard-block policy before command execution.

Primary code:

- `backend/app/ssh_runner.py`
- `backend/mocks/mock_ssh.py`

### Audit & Activity Documentation

Append-only redacted audit trail and ERP activity drafting. Converts command
history and final agent reports into the graded `ActivityCreate` payload.

Primary code:

- `backend/app/audit_log.py`
- `backend/app/activity_generator.py`

### LLM Provider / Agent Contract

Adapter context for Anthropic, OpenAI, Azure OpenAI, and test stub clients.
Builds system/user prompts, parses strict structured responses, and isolates
provider-specific request/response details from the orchestrator.

Primary code:

- `backend/app/agent/llm.py`
- `backend/app/agent/doctrine.py`
- `backend/app/agent/contracts.py`

### Technician Workspace UI

Browser presentation context for ticket list/detail, live troubleshooting
events, command approval/edit/reject controls, retry/abort controls, and final
activity review.

Primary code:

- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`
- `frontend/src/components/*`

## Ubiquitous Language

| Term | Meaning |
|---|---|
| Ticket | Phoenix ERP incident assigned to a technician. |
| TicketStatus | ERP lifecycle state: `OPEN`, `PENDING`, `DONE`. |
| Technician | Authenticated ERP employee using the workspace. |
| Customer | ERP customer associated with a ticket. |
| CustomerSystem | SSH target metadata for the affected customer VM. |
| SystemInfo | IP, port, username, OS, and notes for a customer system. |
| Session | One backend troubleshooting run for one ticket. |
| SessionManager | In-memory registry and factory for sessions. |
| Orchestrator | Deterministic application service that drives the troubleshooting phases. |
| Phase | Workflow state: triage, diagnose, propose_fix, apply, validate, persist_check, document, submit. |
| Hypothesis | Agent-proposed explanation with evidence and confidence. |
| ProposedCommand | Command proposal with purpose, mutating flag, expected effect, and rollback note. |
| Verdict | Safety classification: read-only, needs approval, or hard-block. |
| Classification | Verdict plus reason and matched rule. |
| ApprovalDecision | Technician response: approved, edited, or rejected. |
| CommandResult | Exit code, stdout, stderr, and duration from command execution. |
| AuditEntry | Redacted record of one command/action in the session log. |
| AuditLog | Ordered append-only session history and source for activity generation. |
| FinalReport | Agent's final summary, root cause, actions, command summary, and validation result. |
| ActivityCreate | ERP payload documenting the completed troubleshooting work. |
| SSEEvent | Backend-to-frontend event carrying live session progress. |
| Hard Block | Command class that must never execute, even with human approval. |
| Read-only Batch | Diagnostic command group that can auto-run when `AUTO_RUN_READONLY=true`. |
| Anti-corruption Layer | Boundary adapter that shields app code from external contract details, especially Phoenix ERP. |

## Context Map

| Upstream | Downstream | Relationship |
|---|---|---|
| Phoenix ERP | Phoenix ERP / Ticketing Integration | External upstream system behind `ErpClient` ACL. |
| Phoenix ERP / Ticketing Integration | Troubleshooting Session / Agent Orchestration | Session needs ticket, customer, and customer-system context; later submits activity and status. |
| LLM Provider / Agent Contract | Troubleshooting Session / Agent Orchestration | LLM proposes structured hypotheses and commands; orchestrator validates and enforces. |
| Safety & Redaction Policy | Troubleshooting Session / Agent Orchestration | Shared policy kernel for classifying commands and redacting outputs. |
| Safety & Redaction Policy | Remote Execution | SSH runner rechecks hard-block policy before execution. |
| Remote Execution | Troubleshooting Session / Agent Orchestration | Returns `CommandResult` for approved commands. |
| Troubleshooting Session / Agent Orchestration | Audit & Activity Documentation | Orchestrator appends audit entries and requests activity drafts. |
| Audit & Activity Documentation | Phoenix ERP / Ticketing Integration | Drafted activity is submitted to ERP after technician review. |
| Backend REST/SSE API | Technician Workspace UI | UI consumes backend ticket/session contracts and sends approval decisions. |

Relationship notes:

- Safety & Redaction is a shared kernel with high blast radius.
- `ErpClient` is an ACL for the Phoenix API and token-bearing HTTP calls.
- Frontend DTOs are manually mirrored, not generated.
- Mock ERP and Mock SSH are test-support bounded contexts and may intentionally diverge from production infrastructure internals.

## Aggregates

### Phoenix ERP / Ticketing Integration

- `Ticket` root with `TicketStatus`, customer reference, tags, priority, SLA, and created timestamp.
- `Customer` root with embedded `SystemInfo`.
- `CustomerSystem` root for ticket-specific SSH target information.
- `Activity` root for submitted work documentation.
- `Employee` root for technician identity.

### Troubleshooting Session / Agent Orchestration

- `Session` root for runtime state, SSE history, pending approvals, abort signal, task lifecycle, and activity submission path.
- `Orchestrator` root for the workflow state machine. Children include `Phase`, `Hypothesis`, `ProposedCommand`, `ApprovalDecision`, `FinalReport`, and emitted `SSEEvent`s.

### Safety & Redaction Policy

- Stateless command-policy aggregate: command text maps to `Classification`.
- Policy data includes `HardBlockRule`, read-only allowlists, wrapper parsing, and secret path detection.

### Remote Execution

- `SSHRunner` root for one remote connection/session.
- `CommandResult` is the execution outcome value object.

### Audit & Activity Documentation

- `AuditLog` root with ordered `AuditEntry` children.
- `ActivityCreate` is generated from audit entries plus `FinalReport`.

### Technician Workspace UI

- `AgentWorkspace` root for live frontend session state.
- `TicketList` and `TicketDetail` own local ticket browsing and selection state.

## Domain Events

No separate event bus or event-sourcing model exists. The system does use
SSE event names as integration events between backend and frontend:

- `phase_change`
- `thought`
- `hypotheses`
- `plan`
- `awaiting_approval`
- `command_result`
- `safety_block`
- `validation`
- `persist_check`
- `activity_draft`
- `done`
- `error`

Audit entries are persisted as JSONL records and act as the durable event log
for command/action history inside a session.

## Open Domain Concerns

- Shared models are concentrated in `backend/app/models.py`.
- `Orchestrator` owns broad coordination across several bounded contexts.
- Frontend DTOs manually mirror backend contracts.
- Session state is in memory; audit state is durable JSONL.
