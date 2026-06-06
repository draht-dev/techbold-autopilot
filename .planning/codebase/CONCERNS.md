# Concerns

Generated: 2026-06-06

## Architectural Risks

- Runs are in-memory only. A backend restart loses active run state, event history,
  pending approvals, and open SSH/PTY sessions.
- One run per ticket is documented in the README, but `RunManager` does not enforce
  uniqueness by ticket ID.
- `backend/app/models.py` mixes external Phoenix DTOs with internal run, hypothesis,
  approval, and event contracts. This is a broad shared kernel and a likely source of
  cross-context coupling over time.
- `Run` is a large aggregate: coordination, event history, SSH shell, audit log,
  approvals, hypotheses, activity state, and an ERP reference are all inside one object.
- `backend/app/agent/loop.py` directly depends on `Run`, `SSHRunner`, `TicketStatus`,
  `ActivityCreate`, and `run.erp`, which blurs Diagnostic Agent, Run Coordination,
  Remote Execution, and Service Desk writeback.
- `backend/app/api/ws.py` imports `execute_command` from `agent.tools` for legacy
  one-shot terminal commands, coupling WebSocket transport to the agent tool layer.
- Frontend TypeScript DTOs manually duplicate backend Pydantic models, so REST/event
  contract drift is possible.
- `.planning/codebase/MAP.json` currently classifies the backend coarsely, so internal
  backend bounded contexts require manual analysis rather than relying only on the
  generated graph.
- The backend has broad CORS (`allow_origins=["*"]`), acceptable for hackathon/local
  use but too broad for production.
- Host-key checking is disabled in `SSHRunner.connect()`, which fits the hackathon VM
  assumption but should be revisited for trusted production infrastructure.
- The interactive PTY is intentionally outside per-command safety classification.
  This preserves human control but means raw human terminal sessions rely on the
  technician rather than the deterministic command gate.
- Audit write failures are swallowed to avoid breaking runs; this means disk or
  permission problems could silently degrade persistent auditability while in-memory
  entries continue.

## Test And Quality Gaps

- No frontend unit/component tests.
- No browser e2e tests for the ticket/run/approval/activity workflow.
- No FastAPI route tests or WebSocket protocol tests.
- No coverage tooling or coverage thresholds.
- No lint scripts for backend or frontend.
- No CI workflow is present.

## Repository Hygiene

- The working tree contains ignored runtime/development artifacts such as `.env`,
  `.env.bak`, key files, pycache, local venvs, audit logs, `.DS_Store`, and TypeScript
  build info. They are ignored by Git, but raw scanners can still include them unless
  explicitly filtered.
- `.planning/codebase/STACK.md` was manually cleaned because Draht's initial file-tree
  snapshot included ignored files and venv contents from the local checkout.

## Security Notes

- `.env.example` contains placeholder values only, and `.gitignore` excludes `.env`,
  `.env.bak`, key files, audit logs, and private key extensions.
- Command redaction covers common token, key, URI, password, JWT, PEM, and shadow-hash
  patterns, but new secret formats should be added as they appear.
- Phoenix token, OpenRouter key, and SSH key paths remain backend-only by design.
