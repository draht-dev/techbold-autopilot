# Domain Model Hints

Generated: 2026-06-06

Extracted from the codebase and curated after manual analysis. The authoritative
domain writeup is `.planning/DOMAIN.md`.

## Primary Contract Types

Backend Pydantic contracts:

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

Agent structured-output contracts:

- `HypothesisItem`
- `HypothesesOutput`
- `ProposedFix`
- `CheckOutput`
- `ValidationOutput`
- `ActivityOutput`

Execution/safety/audit contracts:

- `Run`
- `RunManager`
- `ApprovalDecision`
- `ExecResult`
- `Decision`
- `Category`
- `AuditLog`
- `SSHRunner`
- `InteractiveShell`
- `CommandResult`

Frontend mirrored contracts:

- `TicketStatus`
- `Ticket`
- `SystemInfo`
- `CustomerSystem`
- `Employee`
- `HypothesisComment`
- `Hypothesis`
- `ActivityDraft`
- `RunEvent`
- `RunSnapshot`

## Candidate Bounded Contexts

- Service Desk / Phoenix Ticketing
- Troubleshooting Run & Human Control
- Diagnostic Agent
- Command Safety & Audit
- Remote Execution
- Technician Workspace UI
- API / Composition
- Phoenix Mock

## Domain Persistence

- No app-owned relational database tables were found.
- External persistence is Phoenix ERP tickets, status updates, customer-system data,
  and activities.
- Local persistence is per-run audit JSONL.
- The Phoenix mock persists only in process through fixture state: `tickets`,
  `activities`, `_SYSTEMS`, `_CUSTOMER_SYSTEMS`, and `activity_seq`.

## Boundary Watchlist

- `backend/app/models.py` is a broad shared kernel.
- `Run` is a large aggregate with coordination, events, approvals, hypotheses, SSH,
  audit, activity, and ERP references.
- `backend/app/agent/loop.py` spans diagnostic flow, run coordination, remote execution,
  and service-desk writeback.
- Frontend DTOs are manually duplicated from backend contracts.
