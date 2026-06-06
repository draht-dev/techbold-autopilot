# Domain Model Hints

Generated: 2026-06-06 17:00:21 CEST

This file records identifier-level hints from the codebase. The normalized
domain model is in `.planning/DOMAIN.md`.

## Repeated Domain Nouns

- Ticket
- TicketStatus
- Customer
- CustomerSystem
- SystemInfo
- Employee
- Activity
- ActivityCreate
- Session
- SessionManager
- Orchestrator
- Phase
- Hypothesis
- ProposedCommand
- PlanCommand
- AgentResponse
- FinalReport
- Approval
- ApprovalDecision
- CommandResult
- AuditEntry
- AuditLog
- EventType
- SSEEvent
- Verdict
- Classification
- HardBlockRule
- SSHRunner
- ErpClient
- LLM

## Important Functions

- Phoenix ERP: `get_me`, `list_tickets`, `get_ticket`, `get_customer_system`, `get_customer`, `set_status`, `create_activity`, `reset`
- Session API: `create_session`, `start_session`, `stream_session`, `approve_command`, `reject_command`, `retry_session`, `abort_session`, `submit_activity`
- Safety: `classify`, `redact`, `make_redacting_log_filter`
- Agent contract: `build_user_message`, `history_to_contract_dicts`, `parse_agent_response`
- Workflow: `Orchestrator.run`, `Orchestrator.submit`, `Session.start`, `Session.retry`, `Session.abort`
- Execution: `SSHRunner.connect`, `SSHRunner.run`, `SSHRunner.close`, `resolve_key_paths`
- Documentation: `AuditLog.append`, `AuditLog.executed_entries`, `build_activity`

## Storage And Collections

There is no application database in this repository.

- Mock ERP fixtures: `backend/mocks/fixtures/tickets.json`, `customer_systems.json`, `customers.json`
- Mock ERP in-memory collections: `_tickets`, `_activities`
- Audit persistence: JSONL files under `backend/data/audit/<session>.jsonl`
- Runtime sessions: in-memory `SessionManager` dictionary

## Context Candidates

- Phoenix ERP / Ticketing Integration
- Troubleshooting Session / Agent Orchestration
- Safety & Redaction Policy
- Remote Execution
- Audit & Activity Documentation
- LLM Provider / Agent Contract
- Technician Workspace UI
