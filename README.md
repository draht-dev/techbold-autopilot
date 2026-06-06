# techbold — AI Service Desk Autopilot

A supervised AI agent that reads assigned tickets from the Phoenix ERP, connects to the affected Linux VM over SSH, diagnoses the root cause from live system evidence, proposes a minimal fix, and writes a complete activity back to the ERP — with a human-in-the-loop safety model where every mutating command is individually approved before it runs, HARD_BLOCK commands are refused even if a human attempts to approve them, and all output is redacted at every boundary before it is stored, displayed, or fed back to the model.

---

## Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd techbold

# 2. Copy the env template and fill in your values
cp .env.example .env
#   Set PHOENIX_API_BASE_URL and PHOENIX_API_TOKEN (from Builder Base).
#   Set ANTHROPIC_API_KEY (or your chosen LLM provider key).

# 3. Place your SSH private key (from Builder Base) under keys/
cp /path/to/your-key.pem keys/your-key.pem
chmod 600 keys/your-key.pem
#   The value of SSH_PRIVATE_KEY_PATH in .env should match: keys/your-key.pem
```

`.env` and `keys/` are git-ignored. Never commit secrets or private keys.

---

## Run

### Docker (recommended)

```bash
docker compose up --build
```

- Backend API: http://localhost:8000 (Swagger at http://localhost:8000/docs)
- Frontend workspace: http://localhost:5173

The backend container mounts `./keys` read-only at `/keys`. Make sure `SSH_PRIVATE_KEY_PATH=keys/your-key.pem` in your `.env`.

### Local (without Docker)

**Backend** (requires Python 3.11–3.13; Python 3.14 is not yet supported because `pydantic-core` and `asyncssh` wheels are not available for it):

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

**Frontend** (new terminal, requires Node 20+):

```bash
cd frontend
npm install
npm run dev
```

---

## Environment variables

| Variable | Meaning | Default |
|---|---|---|
| `PHOENIX_API_BASE_URL` | Base URL of the Phoenix ERP | _(required)_ |
| `PHOENIX_API_TOKEN` | Bearer token for ERP authentication | _(required)_ |
| `SSH_PRIVATE_KEY_PATH` | Path to the SSH private key file | `keys/your-key.pem` |
| `SSH_USERNAME` | Fallback SSH username if customer-system record is empty | `azureuser` |
| `SSH_DEFAULT_PORT` | Fallback SSH port if customer-system record is empty | `22` |
| `SSH_CONNECT_TIMEOUT` | SSH connection timeout in seconds | `10` |
| `SSH_COMMAND_TIMEOUT` | Per-command execution timeout in seconds | `30` |
| `SSH_COMMAND_TIMEOUT_MAX` | Hard upper bound for any per-command timeout | `120` |
| `LLM_PROVIDER` | LLM provider: `anthropic`, `openai`, or `azure-openai` | `anthropic` |
| `LLM_MODEL` | Model name/ID for the chosen provider | _(required)_ |
| `ANTHROPIC_API_KEY` | Anthropic API key (when `LLM_PROVIDER=anthropic`) | — |
| `OPENAI_API_KEY` | OpenAI API key (when `LLM_PROVIDER=openai`) | — |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key (when `LLM_PROVIDER=azure-openai`) | — |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | — |
| `AZURE_OPENAI_DEPLOYMENT` | Azure OpenAI deployment name | — |
| `AUTO_RUN_READONLY` | Auto-execute read-only diagnosis batch without per-command confirmation | `true` |
| `AGENT_MAX_STEPS` | Maximum LLM iterations per ticket (safety cap) | `40` |
| `AGENT_MAX_COMMANDS` | Maximum SSH commands executed per ticket (safety cap) | `60` |
| `VITE_API_BASE` | URL the browser uses to reach the backend | `http://localhost:8000` |

---

## Architecture

### The agent loop

```
Ticket + customer-system loaded from ERP
          |
          v
    [ TRIAGE ]
    Identify broken capability and expected healthy state.
    Connection gate: technician approves SSH connect before anything runs.
          |
          v
    [ DIAGNOSE ]  <-------------------------------+
    LLM proposes a batch of read-only checks.     |
    Safety classifier: all READ_ONLY              |
    Auto-run (if AUTO_RUN_READONLY) or batch-     |
    confirm. Redacted output fed back.            |
    Repeat until root cause is identified.        |
          |                                       |
          v                                       |
    [ PROPOSE_FIX ]                               |
    LLM emits ordered list of mutating commands,  |
    each with purpose, expected effect, rollback. |
          |                                       |
          v                                       |
    [ APPLY ]  -- for each mutating command:      |
       safety classify                            |
       if HARD_BLOCK  --> auto-reject, log, feed  |
                          back, agent must find   |
                          a safe alternative -----+
       if NEEDS_APPROVAL --> await human gate
          (Approve / Edit & Approve / Reject)
       Run via SSH runner (which re-checks
       HARD_BLOCK as defense in depth).
       Redact output. Append to audit log.
          |
          v
    [ VALIDATE ]
    LLM proposes concrete checks: service active+enabled,
    port listening, curl GET localhost, file perms.
    READ_ONLY auto-run. Proof captured for activity.
          |
          v
    [ PERSIST_CHECK ]
    Verify fix survives restart: systemctl is-enabled,
    config on disk, fstab entry. Restart affected service
    and re-validate.
          |
          v
    [ DOCUMENT ]
    activity_generator builds 8-field ERP activity
    from audit log + final report.
    Human reviews and edits in the UI.
          |
          v
    [ SUBMIT ]
    POST activity to ERP. PATCH ticket status DONE.
```

**Core invariant:** the LLM never calls the SSH runner directly. Every command passes through the safety classifier. `HARD_BLOCK` commands are never executed even if a human approves them (the SSH runner re-checks as a second line of defense). All SSH output is passed through `redact()` before it is stored, displayed, or fed back to the model.

### Modules involved

```
Frontend (React/Vite/TS)
  |  SSE stream + REST
  v
FastAPI backend (app/main.py)
  |-- routers/tickets.py    ERP proxy endpoints
  |-- routers/agent.py      Session management + SSE + approval gates
  |
  |-- session.py            SessionManager + per-session state + approval futures
  |-- agent/
  |     orchestrator.py     State machine (enforces all gates)
  |     llm.py              Provider-abstracted LLM client
  |     doctrine.py         System prompt (diagnostic doctrine)
  |     contracts.py        Structured LLM request/response schema
  |
  |-- safety/
  |     classifier.py       classify(command) -> READ_ONLY | NEEDS_APPROVAL | HARD_BLOCK
  |     rules.py            Hard-block patterns + read-only allowlist
  |     redaction.py        redact(text) -> secret-free text
  |
  |-- ssh_runner.py         Async SSH (asyncssh), re-checks HARD_BLOCK, timeouts
  |-- erp_client.py         Phoenix ERP HTTP client (retries, typed errors)
  |-- audit_log.py          Append-only JSONL per session (memory + disk)
  |-- activity_generator.py Builds 8-field ERP activity from audit log
  |-- config.py             pydantic-settings; loads .env
  |-- models.py             Pydantic models shared across modules
```

---

## Module map

| Module | File | Responsibility |
|---|---|---|
| **erp_client** | `app/erp_client.py` | Typed async HTTP client for all 8 Phoenix ERP endpoints. 15 s timeout, exponential-backoff retry on 5xx, typed `ErpAuthError`/`ErpError` for 401/other. |
| **ssh_runner** | `app/ssh_runner.py` | Async SSH command execution via `asyncssh`. Reuses one connection per session. Re-checks `classify()` and raises `HardBlockedError` on HARD_BLOCK (defense in depth). Clamps timeouts. One reconnect attempt on drop. |
| **safety** | `app/safety/classifier.py`, `rules.py`, `redaction.py` | `classify(command)` splits pipelines/`&&`/`;`, strips `sudo`, and returns the most restrictive verdict across all segments. `redact(text)` replaces passwords, DB URIs, API keys, key blocks, and shadow lines with `«redacted:reason»`. |
| **agent** | `app/agent/orchestrator.py`, `llm.py`, `doctrine.py`, `contracts.py` | The state machine that drives the troubleshooting loop. The LLM proposes; the orchestrator enforces all gates. Structured JSON contracts; reprompts on malformed output. Provider-abstracted LLM client (Anthropic/OpenAI/Azure). |
| **audit_log** | `app/audit_log.py` | Append-only in-memory + JSONL log (`backend/data/audit/<session>.jsonl`). One entry per command with phase, classification, approval, exit code, and redacted output. Single source of truth for the activity. |
| **activity_generator** | `app/activity_generator.py` | Builds the 8-field `ActivityCreate` from the audit log and the agent's `FinalReport`. All fields passed through `redact()` before being handed to the ERP. |
| **session / routers** | `app/session.py`, `app/routers/` | `SessionManager` owns the in-memory session dict. `Session` implements the `SessionIO` protocol (SSE emit, approval futures, retry/abort). Routers expose the HTTP + SSE surface. |

---

## Human-in-the-loop and safety model

### Three verdict tiers

| Verdict | Meaning | What happens |
|---|---|---|
| `READ_ONLY` | Safe diagnostic command | Auto-run (if `AUTO_RUN_READONLY=true`) or batch-confirm; always logged |
| `NEEDS_APPROVAL` | Mutating command | Paused; sent to the technician as `awaiting_approval` SSE event; runs only after explicit Approve (or Edit & Approve) |
| `HARD_BLOCK` | Absolutely forbidden | Never runs regardless of human input; the attempt is logged as a `safety_block` SSE event; rejection is fed back to the LLM with an instruction to find a safe alternative |

### Plan-and-confirm step

Before any mutating commands run, the orchestrator emits the full proposed plan (command, purpose, expected effect, rollback note) as a single `plan` SSE event. The technician sees all proposed commands before committing to any of them.

### Per-command approval gate

Each mutating command triggers an `awaiting_approval` event. The technician can:
- **Approve** — run as proposed.
- **Edit and Approve** — modify the command text, then run the edited version (also re-classified before running).
- **Reject** — skip this command; the rejection is fed back to the LLM.

### Global controls

- **Retry** (`POST /api/agent/sessions/{sid}/retry`) — re-runs the last failed or rejected step.
- **Abort** (`POST /api/agent/sessions/{sid}/abort`) — stops the loop immediately at any phase.

### HARD_BLOCK ruleset

Commands matching any of the following patterns are blocked unconditionally:

- Recursive deletion of system roots (`rm -rf /`, `/etc`, `/var`, `/home`, `/usr`, etc.)
- World-open recursive `chmod` (`chmod -R 777`, `chmod -R a+rwx` on system paths)
- Broad recursive `chown`/`chgrp` on system roots
- Database destruction (`DROP DATABASE`, `DROP TABLE`, `TRUNCATE`, `DELETE FROM` without `WHERE`, `rm` on DB data dirs)
- Disabling security controls (`ufw disable`, `iptables -F`, `systemctl disable ufw/firewalld/auditd/apparmor`, `setenforce 0`)
- Secret exposure (reading `/etc/shadow`, private key files, `*.pem`)
- Hiding actions (`history -c`, truncating `~/.bash_history` or `/var/log/*`, `journalctl --vacuum-*`)
- Privilege escalation via unit files (`User=root` in app/DB units)
- Catastrophic storage commands (`dd of=/dev/sd*`, `mkfs` on mounted disk, fork bombs)

A targeted `chown app:app /var/www/uploads` or `chmod 640 /etc/app/config.yml` is `NEEDS_APPROVAL`, not HARD_BLOCK.

### Redaction boundary

`redact()` is applied at every output boundary:
1. SSH command stdout/stderr — before storage in the audit log, display in the UI, or feed-back to the LLM.
2. Customer-system notes — before returning from `GET /api/tickets/{id}/customer-system`.
3. ERP activity fields — before submitting via `activity_generator`.
4. Application logs — a redacting `logging.Filter` is attached to all log handlers at startup.

---

## Assumptions

- Customer VMs run a modern Linux distribution. The `azureuser` account has passwordless `sudo`.
- A single technician uses the system at a time; sessions are held in memory and lost on restart (the audit log on disk survives).
- The LLM API key is supplied by the operator (not provided by the hackathon organisers).
- `SSH_USERNAME` and `SSH_DEFAULT_PORT` are fallbacks used when the ERP customer-system record has no username/port. Per-ticket values from the ERP take precedence.

---

## Running tests and mocks

### Unit and integration tests (fully offline)

```bash
cd backend
.venv/bin/python -m pytest -q
```

This runs:
- `tests/test_safety_classifier.py` — comprehensive HARD_BLOCK, NEEDS_APPROVAL, and READ_ONLY assertions including pipelines and sudo stripping.
- `tests/test_redaction.py` — asserts secrets are redacted and ordinary diagnostic output is preserved.
- `tests/test_erp_client.py` — ERP client against the in-process mock ERP.
- `tests/test_activity_generator.py` — asserts all 8 activity fields are populated and `commands_summary` is secret-free.

No real ERP, SSH host, or LLM key is required.

### Running the mock ERP for a live demo

Start the standalone mock ERP server (from the `backend/` directory):

```bash
cd backend
.venv/bin/python -m uvicorn mocks.mock_erp:app --port 9000 --reload
```

Then point your `.env` at the mock:

```
PHOENIX_API_BASE_URL=http://localhost:9000
PHOENIX_API_TOKEN=any-value-works
```

If the backend is running inside Docker and the mock ERP is on the host:

```
PHOENIX_API_BASE_URL=http://host.docker.internal:9000
```

The mock ERP accepts any non-empty Bearer token and returns realistic sample tickets, customer-systems, and customers loaded from `mocks/fixtures/`.

---

## Reset usage

The **Reset environment** button in the UI (or `POST /api/dev/reset`) calls the ERP's `POST /api/v1/me/reset` endpoint, which clears all activities submitted under your token and reboots your assigned VMs back to a clean state. Use this between demo runs to start fresh.

---

## Troubleshooting

**401 from the ERP**
Check that `PHOENIX_API_TOKEN` in `.env` is set to your team's token and that the `Authorization: Bearer <token>` header is being sent. The backend logs a clear "check PHOENIX_API_TOKEN" message on 401.

**Empty ticket list**
Your token must be associated with assigned tickets on the ERP. Verify with `curl -H "Authorization: Bearer <your-token>" $PHOENIX_API_BASE_URL/api/v1/me/tickets`.

**SSH connect failure**
- Verify the key file exists at `SSH_PRIVATE_KEY_PATH` and has permissions `chmod 600 keys/your-key.pem`.
- Check `SSH_USERNAME` (default `azureuser`) and `SSH_DEFAULT_PORT` (default `22`) match the VM.
- Confirm the VM is reachable from where the backend runs (`ping <vm-ip>`).

**LLM calls fail**
The event organisers do not provide an LLM key. Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` / Azure variables) in `.env` with your own credentials.

**Docker container cannot reach a locally-run mock ERP**
Use `http://host.docker.internal:9000` instead of `http://localhost:9000` for `PHOENIX_API_BASE_URL`. The `docker-compose.yml` already adds `host.docker.internal` as an extra host.

**Python 3.14 wheel errors during `pip install`**
Use Python 3.11, 3.12, or 3.13. Wheels for `pydantic-core` and `asyncssh` are not yet published for Python 3.14. The Docker image uses `python:3.11-slim`.
