# Domain Model Hints

Generated: 2026-06-07

## Candidate Bounded Contexts

- Phoenix ERP Integration: `backend/app/erp`, `backend/app/mock`,
  `docs/phoenix-openapi.yaml`.
- Incident Run Lifecycle: `backend/app/runs`, `backend/app/api/routes.py`,
  `backend/app/api/ws.py`.
- Agent Investigation: `backend/app/agent`.
- Command Safety, Execution, and Audit: `backend/app/safety`,
  `backend/app/agent/tools.py`, `backend/app/audit`.
- Customer System Access: `backend/app/ssh`.
- Technician Console: `frontend/src/api`, `frontend/src/pages`,
  `frontend/src/components`.

## Entity And Value Object Candidates

### Phoenix / Ticketing

- `Ticket`
- `TicketStatus`
- `Employee`
- `Customer`
- `CustomerSystem`
- `SystemInfo`
- `ActivityCreate`
- `Activity`
- `PhoenixClient`
- `PhoenixError`

### Run Lifecycle

- `Run`
- `RunManager`
- `RunPhase`
- `RunSnapshot`
- `RunSummary`
- `ApprovalDecision`
- `Hypothesis`
- `HypothesisComment`
- `EventType`
- `RunEvent`
- `ResolutionStore`

### Agent Investigation

- `AgentSession`
- `RunCommand`
- `PresentHypotheses`
- `HypothesisInput`
- `ProposeFix`
- `RequestDecision`
- `Finish`
- `HypothesesOutput`
- `HypothesisItem`
- `CheckOutput`
- `ProposedFix`
- `ValidationOutput`
- `ActivityOutput`
- `LLM`
- `LLMError`

### Command Safety And Execution

- `Decision`
- `Category`
- `ExecResult`
- `AuditLog`
- `SSHRunner`
- `InteractiveShell`
- `CommandResult`
- `SSHError`

## Existing Event Names

Outbound WebSocket events:

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

Audit record types observed:

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
