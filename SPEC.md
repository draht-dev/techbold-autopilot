# BUILD SPEC — techbold "AI Service Desk Autopilot"

> **For the coding agent (ultracode):** You also have the official case/requirements doc and the `docs/scoring.md` rubric. This spec is the authoritative engineering blueprint. Build the **entire project in one pass** on top of the provided template repo. Produce a runnable system; the team will optimize afterward. Where this spec and the case doc agree, follow them exactly. Optimize the build effort toward categories **B (Troubleshooting, 35)** and **C (Safety, 20)** — that is where the track is scored and tie-broken.

---

## 0. Non-negotiables (read first)

1. **The LLM can never execute a command.** It only *proposes*. Execution is mediated by the safety layer + the human approval gate + the SSH runner. This is the core safety architecture — do not let the agent call the SSH runner directly.
2. **Every command is classified by the safety layer before it can run**, and the SSH runner itself re-checks for hard-blocks (defense in depth).
3. **A fix is not "done" until it is persistent.** If a service is started it must also be `enable`d; config/unit edits go to disk, not runtime; mounts go in `/etc/fstab`. The grader reboots/restarts VMs.
4. **No secrets anywhere** they can be scored against you: not in the activity, the frontend, the repo, the logs, or screenshots. Redact at the boundary.
5. **Do not hardcode incidents.** The five graded incidents are hidden and run on fresh VMs. The agent must be evidence-driven from live system state, using a general Linux service-diagnosis methodology. Special-casing known problems = fails generalisation.
6. **Minimal, reversible changes only.** Touch only what's needed; keep restarts proportionate; never broaden permissions recursively across system paths.

---

## 1. Tech stack & repository

Build on `START-Vienna/techbold_track_template` (already has Docker compose: backend `:8000`, frontend `:5173`, `docs/phoenix-openapi.yaml`, `.env.example`, `keys/`, MIT `LICENSE`).

- **Backend:** Python 3.11+, FastAPI, `asyncssh` (async SSH + clean timeouts), `httpx` (ERP + LLM HTTP), `pydantic` v2, `pytest`. Async throughout.
- **Frontend:** React + Vite + TypeScript (existing skeleton). Minimal dependencies; functional over fancy.
- **LLM:** provider-abstracted; default **Anthropic** (`ANTHROPIC_API_KEY`, model via `LLM_MODEL`). Must be swappable to OpenAI/Azure via config without touching agent logic.
- **Transport for live agent updates:** **SSE** (Server-Sent Events) from FastAPI to the React workspace. Approvals flow back via POST endpoints.
- **State:** in-memory `SessionManager` (single technician, single backend) is fine, BUT the **audit log is persisted to disk** (JSONL per session under `backend/data/audit/`).

### 1.1 Target file tree

```
backend/
  app/
    main.py                 # FastAPI app, routers, CORS, SSE
    config.py               # pydantic-settings; loads .env
    models.py               # pydantic models (ERP entities, activity, agent contracts, events)
    erp_client.py           # MODULE: Phoenix ERP REST client
    ssh_runner.py           # MODULE: SSH execution (timeouts, re-checks hard-block)
    safety/
      __init__.py
      classifier.py         # MODULE: classify command -> READ_ONLY | NEEDS_APPROVAL | HARD_BLOCK
      rules.py              # rule tables (allowlist, denylist patterns)
      redaction.py          # secret redaction utility
    agent/
      __init__.py
      orchestrator.py       # MODULE: the state machine (the loop)
      llm.py                # provider-abstracted LLM client (default Anthropic)
      doctrine.py           # system prompt text (the diagnostic doctrine) + prompt builders
      contracts.py          # the structured request/response schema for the model
    audit_log.py            # MODULE: append-only structured session log (memory + JSONL)
    activity_generator.py   # MODULE: build the ERP activity from the audit log + final state
    session.py              # SessionManager + Session (per-ticket troubleshooting session)
    routers/
      tickets.py            # ERP-backed ticket endpoints
      agent.py              # start session, SSE stream, approve/edit/reject/abort, submit
  tests/
    test_safety_classifier.py   # MOST IMPORTANT test file
    test_redaction.py
    test_erp_client.py          # against mock ERP
    test_activity_generator.py
    test_agent_loop_mock.py     # full loop against mock SSH + mock LLM
  mocks/
    mock_erp.py             # standalone FastAPI app implementing the 8 ERP endpoints + sample data
    mock_ssh.py             # fake SSH runner returning canned outputs for a sample incident
    fixtures/               # sample tickets, customer-systems, canned command outputs
  requirements.txt
  Dockerfile                # keep/adapt from template
frontend/
  src/
    api.ts                  # typed calls to backend + SSE subscription
    types.ts                # mirrors backend models
    App.tsx
    components/
      TicketList.tsx
      TicketDetail.tsx
      AgentWorkspace.tsx    # live progress + log + approval controls
      CommandApproval.tsx   # approve / edit / reject one proposed command
      HypothesisPanel.tsx
      ActivityReview.tsx    # review + submit final activity
      Controls.tsx          # global Retry / Abort, Reset-environment (dev)
  Dockerfile                # keep/adapt from template
docs/
  phoenix-openapi.yaml      # provided
  scoring.md                # provided
.env.example                # extend (see §3) — NO secrets
.gitignore                  # ensure .env, keys/, backend/data/ ignored
docker-compose.yml          # keep/adapt
README.md                   # rewrite per §11
REPORT.md                   # optional technical write-up (recommended)
```

Keep `erp_client`, `ssh_runner`, `safety`, `agent`, `audit_log`, `activity_generator` as **separate, independently testable modules** — category E rewards exactly this separation.

---

## 2. ERP API contract (Phoenix mock)

Base URL + Bearer token from env. All requests send `Authorization: Bearer <PHOENIX_API_TOKEN>`. Full contract is in `docs/phoenix-openapi.yaml`; consume that as source of truth. Endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/me` | Logged-in technician |
| GET | `/api/v1/me/tickets?status=&priority=&sort=` | Assigned tickets |
| GET | `/api/v1/tickets/{id}` | One ticket |
| GET | `/api/v1/tickets/{id}/customer-system` | SSH target `{ip, port, username, os, notes}` |
| GET | `/api/v1/customers/{id}` | Customer + system info |
| PATCH | `/api/v1/tickets/{id}/status` | Set `OPEN` / `PENDING` / `DONE` |
| POST | `/api/v1/activities/create` | Write the activity back to ERP |
| POST | `/api/v1/me/reset` | Clear your activities + reboot your VMs |

### 2.1 Activity payload (GRADED — must be complete)

```json
{
  "ticket_id": 7001,
  "start_datetime": "2026-06-07T10:00:00Z",
  "end_datetime":   "2026-06-07T10:25:00Z",
  "summary": "One-sentence summary of what was restored.",
  "root_cause": "The technical root cause — not the symptom.",
  "actions_taken": "Diagnosis and fix steps, in order.",
  "commands_summary": "Relevant commands / command classes — no secrets.",
  "validation_result": "Concrete proof the customer benefit is restored."
}
```

Field rules the generator MUST honour: `root_cause` = the technical cause (e.g. "nginx unit not enabled after package reconfigure", not "website was down"); `actions_taken` = ordered diagnosis→fix steps; `commands_summary` = relevant commands or command **classes**, secret-free; `validation_result` = concrete proof (HTTP 200, port listening, `is-active`+`is-enabled`, query succeeds).

### 2.2 ERP client requirements

- Typed methods for all 8 endpoints, returning pydantic models.
- `timeout` on every call (default 15s), retry with exponential backoff on 5xx/network (max 3), no retry on 4xx.
- Graceful handling of **401** (clear "check token" error), **404** (return None / typed not-found, don't crash the workflow), and **empty** ticket lists.
- A thin `set_status(ticket_id, status)` and `create_activity(activity)` and `reset()`.

---

## 3. Configuration / `.env`

Extend `.env.example` (commit it; **no real values**):

```
# Phoenix ERP
PHOENIX_API_BASE_URL=
PHOENIX_API_TOKEN=

# SSH to customer VMs (key path is git-ignored under keys/)
SSH_PRIVATE_KEY_PATH=keys/your-key.pem
SSH_USERNAME=azureuser            # fallback if customer-system.username is empty
SSH_DEFAULT_PORT=22               # fallback if customer-system.port is empty
SSH_CONNECT_TIMEOUT=10
SSH_COMMAND_TIMEOUT=30            # per-command default; allow per-command override up to SSH_COMMAND_TIMEOUT_MAX
SSH_COMMAND_TIMEOUT_MAX=120

# LLM (bring your own; default Anthropic)
LLM_PROVIDER=anthropic            # anthropic | openai | azure-openai
LLM_MODEL=                        # e.g. a strong reasoning Claude model
ANTHROPIC_API_KEY=
# OPENAI_API_KEY= / AZURE_OPENAI_* as needed

# Agent behaviour
AUTO_RUN_READONLY=true            # auto-run read-only diagnosis after batch is shown; mutating always gated
AGENT_MAX_STEPS=40                # safety cap on model iterations per ticket
AGENT_MAX_COMMANDS=60             # safety cap on executed commands per ticket

# Frontend
VITE_API_BASE=http://localhost:8000
```

`config.py` uses `pydantic-settings`. Resolve SSH connection per ticket as: `username = customer_system.username or SSH_USERNAME`; `port = customer_system.port or SSH_DEFAULT_PORT`. Assume `azureuser` has passwordless `sudo`; if a `sudo` command fails on auth, surface a clear error and let the agent adapt.

---

## 4. The safety layer (CATEGORY C — build this carefully)

`safety/classifier.py` exposes:

```python
class Verdict(str, Enum):
    READ_ONLY = "read_only"        # safe to run, auto-runnable in a diagnosis batch
    NEEDS_APPROVAL = "needs_approval"  # mutating; requires explicit human approval
    HARD_BLOCK = "hard_block"      # never executable, even if a human approves

@dataclass
class Classification:
    verdict: Verdict
    reason: str
    matched_rule: str | None

def classify(command: str) -> Classification: ...
```

**Order of evaluation:** (1) hard-block patterns → if any match, `HARD_BLOCK`. (2) read-only allowlist → `READ_ONLY`. (3) everything else (including unrecognised commands) → `NEEDS_APPROVAL` (never auto-run the unknown). Parse with `shlex`; handle `sudo` by classifying the *underlying* command (do not blanket-block `sudo`). Handle pipelines/`&&`/`;` by classifying **each** segment and taking the most restrictive verdict.

### 4.1 HARD_BLOCK ruleset (absolute — these mirror the rubric's named hard-fails)

| Class | Block when matching (examples, implement as tested patterns) |
|---|---|
| Recursive system deletion | `rm` with `-r`/`-rf`/`-fr` targeting `/`, `/*`, `~`, `/etc`, `/var`, `/var/lib`, `/var/lib/postgresql`, `/var/lib/mysql`, `/home`, `/srv`, `/boot`, `/usr`, `/lib`, `/bin`, `/sbin`, `/root` |
| World-open recursion | `chmod -R 777` / `chmod -R a+rwx` / mode `0777` (recursive) on `/`, `/etc`, `/var`, `/srv`, `/home`, `/usr`; also non-recursive `chmod 777` on those system roots |
| Broad recursive chown | recursive `chown`/`chgrp` on the system roots above |
| DB destruction | `DROP DATABASE`, `DROP TABLE`, `TRUNCATE`, `dropdb`, `mysqladmin ... drop`, `DELETE FROM` without a `WHERE`, or `rm` on a DB data dir |
| Disabling security | `ufw disable`, `iptables -F`/`-X` (flush), `nft flush ruleset`, `systemctl stop\|disable (ufw\|firewalld\|nftables\|auditd\|apparmor)`, `setenforce 0` |
| Secret exposure | reading `/etc/shadow`, any private key file (`id_rsa`, `*.pem`, `BEGIN ... PRIVATE KEY`); writing env/secret dumps to a file or into the activity |
| Hiding actions | `history -c`, `rm`/`truncate`/`> ` on `~/.bash_history` or `/var/log/*`, `journalctl --vacuum-*` used to clear |
| Priv-esc to dodge DB perms | editing a unit/app config to set the app's run user to `root` (`User=root`), or starting the app/DB as root to bypass permissions |
| Catastrophic | fork bombs, `dd of=/dev/sd*`, `mkfs` on a mounted disk, `> /dev/sd*` |

When the agent proposes a HARD_BLOCK command: **do not run it**, log the attempt + reason, and feed the rejection back into the agent context with an instruction to find a safe, minimal alternative (e.g. "fix the directory ownership, do not run the service as root"; "open the specific port instead of disabling the firewall").

### 4.2 READ_ONLY allowlist (auto-runnable, still logged)

`cat`, `head`, `tail`, `less`, `ls`, `stat`, `file`, `readlink`, `grep`/`egrep` (no `-r` write), `find` (no `-delete`/`-exec rm`), `journalctl` (read), `systemctl status|is-active|is-enabled|list-units|list-unit-files|cat`, `ss`, `netstat`, `ps`, `top -bn1`, `df`, `df -i`, `du`, `free`, `uptime`, `dmesg`, `id`, `whoami`, `getent`, `ip a`, `ip r`, `ping -c`, `nginx -t`, `apachectl configtest`, `sshd -t`, `named-checkconf`, `findmnt`, `mount` (no args = list), `getcap`, `crontab -l`, `date`, `hostnamectl`, `lsblk`, `systemd-analyze`. Local validation reads: `curl`/`wget` restricted to `GET`/`HEAD` against `localhost`/`127.0.0.1`.

> **Context matters (from the rubric):** a *targeted* `chown app:app /var/www/uploads` or `chmod 640 /etc/app/config` is legitimate `NEEDS_APPROVAL`, not a hard-block. Only **recursive/broad/system-path** operations are forbidden. Do not over-block legitimate fixes.

### 4.3 Secret redaction — `safety/redaction.py`

`redact(text: str) -> str`, applied to all command stdout/stderr before it is **stored in the audit log, shown in the UI, fed into the activity, or written to app logs**. Redact values, keep structure/keys so diagnostics stay useful. Patterns: `PASSWORD=…`, DB URIs `postgres|mysql://user:****@host`, API keys (`sk-…`, `AKIA…`, long hex/base64 tokens), private-key blocks, `/etc/shadow` lines, and the literal `PHOENIX_API_TOKEN` / SSH key bytes if they ever appear. Replace with `«redacted:reason»`. Wire a logging filter so the app's own logs are redacted too.

---

## 5. SSH runner — `ssh_runner.py`

- `async run(command, *, timeout) -> CommandResult{exit_code, stdout, stderr, duration_ms}` over a reused `asyncssh` connection per session.
- Connect timeout `SSH_CONNECT_TIMEOUT`; per-command timeout from arg (clamped to `SSH_COMMAND_TIMEOUT_MAX`).
- **Re-check `classify(command)` and refuse on `HARD_BLOCK`** even if called directly.
- Never logs the key. On timeout/connection drop: typed error + one reconnect attempt.
- Output is passed through `redact()` by the orchestrator before storage/display.

---

## 6. Audit log — `audit_log.py`

Append-only, per session, in memory + JSONL on disk. One entry per action:

```json
{
  "ts": "2026-06-07T10:03:11Z",
  "phase": "diagnose",
  "command": "systemctl status nginx",
  "classification": "read_only",
  "approval": "auto",            // auto | approved | edited | rejected | hard_block
  "approved_by": "technician",   // when human-gated
  "exit_code": 3,
  "output_summary": "«redacted-safe summary»",
  "agent_rationale": "Checking whether nginx is running and enabled"
}
```

This log is **the single source of truth** for the activity (category C audit-trail points + B summary quality). Never write raw secrets here — store the redacted summary only.

---

## 7. The agent — state machine, contract & doctrine

### 7.1 Orchestrator state machine (`agent/orchestrator.py`)

Deterministic phases; the LLM advises, the orchestrator enforces:

```
TRIAGE → DIAGNOSE → PROPOSE_FIX → APPLY → VALIDATE → PERSIST_CHECK → DOCUMENT → SUBMIT
```

- **TRIAGE:** load ticket + customer-system (+ customer). Establish the reported broken capability and expected healthy state. Open the SSH connection only after the session-level **"approve connection & begin diagnosis"** gate.
- **DIAGNOSE (read-only):** the model proposes a batch of read-only checks. Show them as a plan; if `AUTO_RUN_READONLY` run them (still logged), else require one batch confirm. Feed redacted results back. Loop until the model produces ranked hypotheses with a confident root cause (or exhausts read-only ideas). Respect `AGENT_MAX_STEPS`.
- **PROPOSE_FIX:** model emits an ordered list of **mutating** commands, each with purpose, expected effect, rollback note, and an explicit persistence consideration.
- **APPLY (gated):** each mutating command → safety classify → if `HARD_BLOCK`, auto-reject + feed back; else enqueue for **individual human approval** (approve / edit-then-approve / reject). On approve, run through the SSH runner; feed redacted result back.
- **VALIDATE:** model proposes concrete validation (service active+enabled, port listening, local `curl`/`wget` GET, file/permission present, query works). Capture the proof for `validation_result`.
- **PERSIST_CHECK:** verify durability — `systemctl is-enabled`, config/unit on disk, `fstab` entry as relevant; **restart the affected service** (not reboot the VM) and re-validate to prove the fix survives a restart.
- **DOCUMENT:** `activity_generator` builds the 8-field activity from the audit log + final root cause/validation. Human reviews/edits in the UI.
- **SUBMIT:** POST activity, then PATCH ticket status `DONE`. Surface success/failure.

Global **Abort/Stop** must halt the loop immediately at any phase; **Retry** must re-run the last failed/rejected step.

### 7.2 Model interaction contract (`agent/contracts.py`)

The model receives: doctrine (system), and a user message with — ticket, customer-system (secrets redacted), customer info, current phase, full ordered history of `{command, classification, approval, exit_code, redacted_output}`, and current hypotheses. It returns **strict JSON** (use tool-use / structured output; reject + reprompt on malformed JSON):

```json
{
  "phase": "diagnose",
  "thought": "short reasoning",
  "hypotheses": [
    {"cause": "nginx not enabled", "evidence": "is-enabled=disabled; active=inactive", "confidence": 0.8}
  ],
  "proposed_commands": [
    {"command": "sudo systemctl enable --now nginx",
     "purpose": "start nginx and ensure it starts on boot",
     "mutating": true,
     "expected_effect": "active (running) and enabled",
     "rollback": "systemctl disable --now nginx"}
  ],
  "ready_to_validate": false,
  "ready_to_finish": false,
  "final": null
}
```

`final` (only when `ready_to_finish`): `{root_cause, summary, actions_taken, commands_summary, validation_result}` — drafts the human reviews. The orchestrator owns control flow; it ignores any model attempt to skip gates.

### 7.3 Agent operating doctrine (`agent/doctrine.py`) — system prompt content

Encode this as the system prompt. **This governs the B score; keep it general, not incident-specific.**

> You are a senior Linux service technician working **one command at a time under human supervision**. You may only *propose* commands; a human approves and the system runs them. Optimise for: correct root cause, a minimal fix that addresses the underlying cause (never a fragile workaround), durability across reboot, zero collateral damage, and a precise written record.
>
> **Method — always diagnose before you fix:**
> 1. From the ticket and customer-system notes, identify the affected service/capability and its expected healthy state.
> 2. Gather evidence with read-only commands before forming conclusions: service state (`systemctl status`, `is-active`, `is-enabled`, `journalctl -u <unit> --no-pager -n 200`), listening sockets (`ss -tlnp`), config validity (service-specific test), recent logs, resources (`df -h`, `df -i` for inodes, `free -m`), file/ownership/permission state of the relevant paths, dependencies and required services, mounts (`findmnt`, `/etc/fstab`), timers/cron, TLS cert expiry if relevant, and the unit file itself (`ExecStart`, `User`, `WorkingDirectory`, `Environment`).
> 3. Produce **ranked hypotheses with the evidence for each**. Test the cheapest, most likely one first.
>
> **Root-cause families to consider** (a checklist to reason through, not fixes to apply blindly): service stopped/crashed/not enabled; bad or recently-changed config; wrong file/dir ownership or permissions; port conflict or wrong bind address; missing package/dependency; disk full or inodes exhausted; failed/incorrect mount; expired certificate; broken systemd unit; firewall blocking the specific port; DNS/resolver issue; corrupted or locked state file; wrong/missing environment variable; log growth filling the disk.
>
> **Fixing:** make the **smallest change that fixes the cause**. Prefer correcting config/permissions/units over blind restarts. A fix is only complete if it **persists**: if you start a service, also `enable` it; edit on-disk config/units (then `daemon-reload`), not runtime state; put mounts in `/etc/fstab`. After applying, confirm the change is on disk and `is-enabled`, then **restart the affected service and re-validate** to prove it survives a restart.
>
> **Never:** run the application or database as root to bypass a permissions problem — fix the permissions instead. Never disable the firewall/audit/AppArmor to "fix" connectivity — open the specific rule only if that is the actual cause. Never delete data, logs, or history. Never broaden permissions recursively across system paths. Never read or echo secrets into output.
>
> **No regression:** change only what's needed; don't restart unrelated services; keep restarts proportionate.
>
> **Validation must prove the customer's actual benefit** with a concrete check (HTTP 200 from the service, the port listening, the unit active+enabled, the file present with correct perms, the query succeeding) — not just "looks fine".
>
> When you have a confident root cause, validated and persistent fix, set `ready_to_finish` and provide `final` with a technical `root_cause` (not the symptom), ordered `actions_taken`, a secret-free `commands_summary`, and a concrete `validation_result`.

---

## 8. Backend HTTP API (your backend, not the ERP)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness (keep from template) |
| GET | `/api/me` | proxy ERP `/me` |
| GET | `/api/tickets?status=&priority=&sort=&customer=` | tickets (default `sort=date` desc) |
| GET | `/api/tickets/{id}` | ticket detail |
| GET | `/api/tickets/{id}/customer-system` | customer-system (redacted for display) |
| POST | `/api/agent/sessions` `{ticket_id}` | create a troubleshooting session → `{session_id}` |
| POST | `/api/agent/sessions/{sid}/start` | approve connection & begin diagnosis (the connection gate) |
| GET | `/api/agent/sessions/{sid}/stream` | **SSE** event stream (see §9) |
| POST | `/api/agent/sessions/{sid}/approve` `{action_id, edited_command?}` | approve (optionally edited) a proposed command |
| POST | `/api/agent/sessions/{sid}/reject` `{action_id}` | reject a proposed command |
| POST | `/api/agent/sessions/{sid}/retry` | retry last failed/rejected step |
| POST | `/api/agent/sessions/{sid}/abort` | stop the session immediately |
| POST | `/api/agent/sessions/{sid}/activity` `{activity}` | submit the (human-reviewed) activity → ERP, then set status DONE |
| POST | `/api/dev/reset` | call ERP `/me/reset` (dev convenience) |

The agent loop runs as an async task per session; it `await`s an approval event when a mutating command is pending.

---

## 9. SSE event protocol

Each event: `{type, session_id, ts, data}`. Types:

- `phase_change` `{phase}`
- `thought` `{text}`
- `hypotheses` `{items:[{cause,evidence,confidence}]}`
- `plan` `{commands:[{action_id, command, classification, mutating, purpose, expected_effect}]}` (read-only batch shown before running)
- `awaiting_approval` `{action_id, command, classification, purpose, expected_effect, rollback}`
- `command_result` `{action_id, command, classification, approval, exit_code, output_summary}` (output already redacted)
- `safety_block` `{command, reason}` (a HARD_BLOCK the agent tried; shown so the human sees the guardrail working)
- `validation` `{passed, detail}`
- `persist_check` `{passed, detail}`
- `activity_draft` `{activity}` (the 8 fields for human review)
- `done` `{ticket_id, status:"DONE"}`
- `error` `{where, message}`

Visible safety blocks and the running command log are worth showing in the demo — they directly demonstrate C (auditability + human control).

---

## 10. Frontend (CATEGORIES A & D)

- **TicketList:** columns title, customer, priority, status. **Default sort by date (desc).** Sort/filter by status, priority, customer, date. Handle empty + error states (don't blank-screen).
- **TicketDetail:** customer report + customer-system info (IP/port/user/os/notes) — **run notes through redaction before rendering** (no secrets in the frontend). Button: "Start session" → "Approve connection & begin diagnosis".
- **AgentWorkspace:** subscribe to SSE. Show a phase indicator, the live hypotheses panel, and a followable command log (command + classification badge + redacted output + exit code).
- **CommandApproval:** for each `awaiting_approval`, show command, purpose, expected effect, rollback, classification badge; buttons **Approve / Edit & Approve / Reject**. Read-only batches show as a confirmable plan (or auto-run badge if `AUTO_RUN_READONLY`).
- **Controls:** global **Retry** and **Abort/Stop** always visible while a session is active. Dev-only **Reset environment** button.
- **ActivityReview:** render the draft activity (all 8 fields), editable, then **Submit** (POSTs activity + sets DONE). Confirm success.

Keep styling clean and legible; do not spend time on heavy visual polish (out of scope per the case).

---

## 11. README (CATEGORY E) — required sections

Setup; Run (Docker + local, both); Environment variables (table); Architecture (one diagram or clear prose of the loop + modules); Module map (erp_client / ssh_runner / safety / agent / audit_log / activity_generator); Human-in-the-loop & safety model (how approval + hard-blocks + redaction work); Assumptions; Running tests & mocks (how to run the full loop offline against mock ERP + mock SSH); Reset usage; Troubleshooting (401, empty tickets, SSH connect, LLM key, `host.docker.internal` for Docker→local mock). Optional `REPORT.md` for the technical write-up the case invites.

---

## 12. Tests & mocks (CATEGORY E)

- **`test_safety_classifier.py` (highest priority):** assert every HARD_BLOCK pattern in §4.1 is blocked; assert legitimate **targeted** `chmod`/`chown`/`rm` pass as `NEEDS_APPROVAL`; assert read-only commands classify `READ_ONLY`; assert pipelines/`sudo`/`&&` take the most restrictive verdict; assert unknown commands default to `NEEDS_APPROVAL`.
- **`test_redaction.py`:** secrets (passwords, DB URIs, API keys, key blocks, shadow lines) are redacted; ordinary diagnostic output is preserved.
- **`mock_erp.py`:** standalone FastAPI app implementing all 8 endpoints with sample tickets/customer-systems/activities; Bearer-checked. Lets dev + demo run without the real ERP.
- **`mock_ssh.py`:** a fake runner returning canned outputs for one sample incident (e.g. a stopped+disabled service), so `test_agent_loop_mock.py` drives the full loop deterministically with a stubbed LLM.
- **`test_erp_client.py`** (against mock) and **`test_activity_generator.py`** (asserts all 8 fields populated, secret-free `commands_summary`).

---

## 13. Error handling & timeouts (CATEGORY E)

Timeouts + sensible retries on **SSH**, **ERP**, and **LLM** calls, with clear human-readable messages surfaced over SSE (`error` events). Never crash the session loop on a single failed command — log it, inform the human, allow Retry/Abort. Clamp command timeouts. Cap model iterations (`AGENT_MAX_STEPS`) and executed commands (`AGENT_MAX_COMMANDS`) per ticket.

---

## 14. Definition of Done (self-check against the rubric)

**A (20):** tickets load via ERP; usable list with title/customer/priority/status; sort/filter by status|priority|date; customer-system loads for worked tickets; a **complete** activity is created; auth/404/empty don't break the flow.

**B (35):** agent diagnoses root cause from live evidence (no hardcoding); applies a minimal fix that addresses the cause; **fix persists** (`enable` + on-disk config + survives service restart); no regression/data loss; activity summary is complete and technically useful.

**C (20):** complete audit trail (every command + key action logged); no dangerous blanket commands (hard-block ruleset enforced + visibly demonstrated); no secrets in activity/frontend/repo/logs; minimal proportionate changes; human control with approve/edit/reject/retry/abort and a visible plan-and-confirm step.

**D (10):** clear ticket overview + detail with system info; visible agent progress; followable logs/actions; review/retry/abort.

**E (15):** clean separated frontend/backend with modular code (the six modules); a real README; runnable tests + mocks; error handling + timeouts + retries; `.env.example` present with no committed secrets.

**Repo hygiene:** public, MIT `LICENSE` at root; `.env`, `keys/`, `backend/data/` git-ignored; nothing secret committed.

---

## 15. Explicit instructions to the coding agent

- **Do** build the full project in one pass on the template; make `docker compose up --build` bring up a working system, and make the full loop runnable offline against `mock_erp` + `mock_ssh`.
- **Do** keep the six modules separate and independently testable.
- **Do** treat the safety classifier and the agent doctrine as the highest-value code; write the classifier tests first and make them pass.
- **Do** redact at every output boundary.
- **Do not** let the LLM execute commands directly, skip the approval gate for mutating commands, or run any HARD_BLOCK command even if "approved".
- **Do not** hardcode or special-case any specific incident — the graded VMs are unseen.
- **Do not** commit secrets, the SSH key, or `.env`.
- If a design point is ambiguous, prefer the **safer, more minimal, more auditable** option — that is how ties are broken.
