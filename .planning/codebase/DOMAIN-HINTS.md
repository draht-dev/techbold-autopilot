# Domain Model Hints

Generated: 2026-06-07

Extracted from code, generated graph output, and Draht subagent analysis.

## Candidate Bounded Contexts

- Phoenix ERP Integration: external ticket, customer, system, activity, and status
  contract.
- Incident Run Lifecycle: run aggregate, phases, gates, replay, STOP, retention,
  and resolution lookup.
- Agent Investigation: continuous LLM session, tool calls, hypotheses, fix plan,
  validation, and activity drafting.
- Command Safety, Execution, And Audit: command classification, approvals,
  redaction, audit, and terminal echo.
- Customer System Access: SSH command runner and interactive PTY.
- Technician Console: browser workflow and state projection.
- Phoenix Mock: offline substitute for Phoenix during dev/tests.

## Key Domain Terms

- Ticket
- Ticket Status
- Employee
- Customer
- Customer System
- System Info
- Activity
- Activity Draft
- Run
- Run Phase
- Approval
- Decision Request
- Hypothesis
- Hypothesis Comment
- Check
- Proposed Fix
- Validation
- Event / Run Event
- Agent Message
- Auto-Approve Reads
- Safety Decision
- DENY / CONFIRM / ALLOW
- Audit Log
- Resolution
- SSH Runner
- Interactive Shell
- Phoenix Client
- Phoenix Mock
- LLM
- Agent Session
- Tool Call

## Entity And Value Object Candidates

### Backend Pydantic / Python

- `TicketStatus`
- `Employee`
- `Ticket`
- `SystemInfo`
- `CustomerSystem`
- `Customer`
- `StatusUpdate`
- `ActivityCreate`
- `Activity`
- `RunPhase`
- `HypothesisComment`
- `Hypothesis`
- `StartRunRequest`
- `EventType`
- `ApprovalDecision`
- `Run`
- `RunManager`
- `ResolutionStore`
- `ExecResult`
- `Decision`
- `Category`
- `CommandResult`
- `SSHRunner`
- `InteractiveShell`
- `PhoenixClient`
- `PhoenixError`
- `LLM`
- `LLMError`
- `AgentSession`
- `RunCommand`
- `HypothesisInput`
- `PresentHypotheses`
- `ProposeFix`
- `RequestDecision`
- `Finish`

### Frontend TypeScript

- `TicketStatus`
- `Ticket`
- `SystemInfo`
- `CustomerSystem`
- `Employee`
- `HypothesisComment`
- `Hypothesis`
- `ActivityDraft`
- `RunEvent`
- `AgentToolCall`
- `AgentMessage`
- `AgentDecision`
- `RunSnapshot`
- `RunSummary`

## Aggregate Candidates

- Ticket Aggregate: `Ticket` root with customer system, customer, status, and
  activity concepts.
- Run Aggregate: `Run` root with phase, events, approvals, decisions, hypotheses,
  activity draft/submission, terminal replay, and resolution.
- Agent Session Aggregate: `AgentSession` root with conversation messages,
  compaction, and tool-call pairing.
- Command Execution Aggregate: `ExecResult` root with safety decision, SSH result,
  audit entry, and terminal output.
- Remote Execution Aggregate: `SSHRunner` root with command results and
  interactive shell.
- Technician Workspace Projection: `Workspace` state derived from run snapshots
  and WebSocket events.

## Domain Events / Message Names

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

Audit record types:

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

## Boundary Flags

- `Run` is large and central.
- `models.py` mixes external ERP contracts with internal run/event contracts.
- TypeScript DTOs are manually mirrored.
- Agent orchestration mutates `Run` directly.
- Safety/execution infrastructure lives in `backend/app/agent/tools.py`.
