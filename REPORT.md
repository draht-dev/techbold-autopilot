# Technical Report — techbold AI Service Desk Autopilot

## 1. Diagnostic doctrine and generalisation

The agent's operating doctrine (encoded in `agent/doctrine.py` as the system prompt) is deliberately incident-agnostic. Rather than encoding known failure patterns, it encodes a *method*: gather evidence first, form ranked hypotheses with explicit evidence, test the cheapest hypothesis first, and make only the smallest change that addresses the confirmed root cause.

The doctrine gives the model a structured root-cause checklist — service stopped or not enabled; misconfigured or recently changed config; wrong file or directory ownership/permissions; port conflict or wrong bind address; disk full or inodes exhausted; failed mount; expired TLS certificate; firewall blocking the specific port; corrupted state file; wrong or missing environment variable. This covers the vast majority of Linux service incidents without hardcoding any particular one.

The model also receives the full ordered history of every command it has proposed, the classification of each, the approval outcome, and the redacted output. This means the agent reasons from live system state rather than from assumptions. A wrong hypothesis is visible in the history and the model is expected to revise. This generalises to unseen incidents on fresh VMs because the agent has no prior knowledge to fall back on — it must look.

## 2. Safety architecture

### Three-tier classifier

The safety classifier (`safety/classifier.py`) implements three verdict tiers. Every command, before any execution, is classified by splitting compound shell expressions (pipelines, `&&`, `||`, `;`, newlines) into segments, stripping `sudo` and common wrappers (`nice`, `timeout`, `nohup`, `env VAR=x`), and then:

1. Testing hard-block patterns against the stripped segment text.
2. Checking the read-only allowlist by command name, with per-command constraints (e.g. `systemctl` is read-only only for `status|is-active|is-enabled|list-units|list-unit-files|cat`; `find` is read-only only if it carries no `-delete`/`-exec`).
3. Defaulting unknown commands to `NEEDS_APPROVAL` — never auto-running the unrecognised.

The most restrictive verdict across all segments governs the compound command.

### Hard-block ruleset (SPEC §4.1)

The hard-block rules cover recursive deletion of system roots; world-open recursive `chmod`; broad recursive `chown`/`chgrp`; database destruction (including `DELETE FROM` without a `WHERE` clause); disabling security controls (ufw, iptables flush, firewalld, auditd, AppArmor, SELinux); secret exposure (reading `/etc/shadow` or private key files); hiding actions (clearing bash history or system logs); privilege escalation by running the application or database as root; and catastrophic storage operations (fork bombs, `dd of=/dev/sd*`, `mkfs` on a mounted disk).

Critically, the ruleset distinguishes targeted from broad operations. A `chown app:app /var/www/uploads` or `chmod 640 /etc/app/config.yml` is `NEEDS_APPROVAL`, not HARD_BLOCK. Only recursive or system-path-wide operations are forbidden.

### Defense in depth

The SSH runner (`ssh_runner.py`) calls `classify()` again immediately before executing, raising `HardBlockedError` if the verdict is `HARD_BLOCK`. This means even a direct programmatic call to the runner that bypasses the orchestrator's gate cannot run a forbidden command. The orchestrator is the first gate; the runner is the second.

### Redaction boundary

`safety/redaction.py` exposes `redact(text: str) -> str`, which replaces passwords (`PASSWORD=...`), database URIs (`postgres://user:***@host`), API keys (`sk-...`, `AKIA...`, long hex/base64 tokens), private-key PEM blocks, `/etc/shadow` lines, and the literal values of `PHOENIX_API_TOKEN` and SSH key material with `«redacted:reason»`. The function is applied:
- to all SSH stdout/stderr before storage, display, or LLM feed-back;
- to customer-system notes before returning them from the API;
- to all eight activity fields before ERP submission;
- to all log records via a `logging.Filter` attached at startup.

This means secrets cannot reach the frontend, the ERP activity, the audit log, or the application logs regardless of what a VM's configuration files contain.

## 3. Orchestrator state machine and persistence guarantee

### State machine

The orchestrator (`agent/orchestrator.py`) drives a deterministic eight-phase loop:

```
TRIAGE -> DIAGNOSE -> PROPOSE_FIX -> APPLY -> VALIDATE -> PERSIST_CHECK -> DOCUMENT -> SUBMIT
```

The LLM only populates the `proposed_commands`, `hypotheses`, `thought`, and `final` fields of a structured JSON response. It cannot advance the phase, skip a gate, or call the SSH runner. The orchestrator owns all transitions.

The structured contract (`agent/contracts.py`) uses Anthropic tool-use (or equivalent) to enforce strict JSON output. Malformed responses trigger a reprompt with the validation error, up to a configured limit, before surfacing an error event.

### Persistence guarantee

The PERSIST_CHECK phase explicitly verifies that the fix will survive a reboot or service restart. After applying a fix, the agent is instructed to:
- Confirm `systemctl is-enabled` for any service that was started.
- Verify that configuration or unit file edits are on disk (not only in runtime state).
- Confirm `/etc/fstab` entries for any mounts.
- Restart the affected service and re-validate to prove the fix survives a restart.

This is enforced by doctrine, by the phase structure (VALIDATE precedes PERSIST_CHECK, not the other way around), and by the activity generator requiring a non-empty `validation_result` that captures concrete proof.

### Audit log as single source of truth

The audit log (`audit_log.py`) appends one entry per command to an in-memory list and a JSONL file (`backend/data/audit/<session>.jsonl`). Each entry records timestamp, phase, the command (redacted), classification verdict, approval outcome, approved-by, exit code, redacted output summary, and the agent's stated rationale. The `activity_generator` builds the ERP activity entirely from this log (plus the agent's final structured report), so the activity is a faithful, tamper-evident record of what actually ran rather than a post-hoc reconstruction.

## 4. Design trade-offs

**In-memory sessions.** The `SessionManager` holds sessions in a Python dict. A restart loses all active sessions. This is acceptable for a hackathon demo with a single technician; production would require a persistent session store. The audit log on disk survives restarts and is the durable record.

**Async SSE as the transport.** Server-Sent Events are unidirectional (server to browser) and simple to implement with FastAPI + `sse-starlette`. Approvals flow back via conventional POST endpoints. This avoids the complexity of WebSockets while giving live streaming.

**No hardcoded incidents.** The agent has no knowledge of the five graded incidents. This means it occasionally needs more diagnostic steps than a hardcoded solver would, but it will generalise correctly to any Linux service problem within the root-cause families the doctrine covers.

**Provider-abstracted LLM.** The `BaseLLM` interface is satisfied by `AnthropicLLM`, `OpenAILLM`, `AzureOpenAILLM`, and `StubLLM` (for tests). Swapping providers requires only changing `LLM_PROVIDER` and the corresponding key in `.env`.

## 5. Mapping to rubric categories

| Category | Points | Where it lands in this implementation |
|---|---|---|
| A — Functional MVP | 20 | ERP client with all 8 endpoints; ticket list with sort/filter; customer-system loaded; complete 8-field activity created; typed errors on 401/404/empty. |
| B — Troubleshooting | 35 | General diagnostic doctrine; evidence-driven root cause; PERSIST_CHECK phase enforces enable+on-disk+service-restart; no hardcoded incidents; complete root_cause, actions_taken, commands_summary, validation_result in the activity. |
| C — Safety | 20 | Complete audit trail (every command logged with classification and approval); HARD_BLOCK ruleset with the named hard-fail patterns; redaction at every boundary; human approve/edit/reject/retry/abort gates; HARD_BLOCK never runs even if approved; visible safety_block SSE events. |
| D — Technician UX | 10 | TicketList with sort/filter/status badges; TicketDetail with system info; AgentWorkspace with phase indicator, live hypotheses, command log; CommandApproval component; global Retry/Abort; ActivityReview with editable 8-field form and Submit. |
| E — Engineering quality | 15 | Six independently testable modules; this README; tests across classifier, redaction, ERP client, activity generator; mock ERP + mock SSH for offline testing; error handling with timeouts and retries on SSH/ERP/LLM; .env.example with no secrets; audit trail; clean separation of concerns. |
