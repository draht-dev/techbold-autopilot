# AI Service Desk Autopilot

An AI-assisted technician workspace for the techbold START Hack track. It pulls
tickets from the Phoenix ERP, connects to a customer Linux VM over SSH, and
diagnoses + fixes the incident **under the technician's control** using a
Cursor-Debug-Mode-style loop, then writes a clean activity back to the ERP.

> Every action on the VM is gated. A deterministic safety layer blocks dangerous
> commands outright, mutations require explicit human approval, and all command
> output is redacted before it is logged, shown, or written to the ERP.

---

## 1. The flow

```
load ticket -> read customer system -> approve SSH connect -> read-only recon
   -> ranked hypotheses with % likelihood (pick one, comment, or write your own)
   -> check (runs every check command of the hypothesis) -> on confirm, propose a
   minimal fix (technician approves) -> apply -> validate (concrete proof)
   -> verify persistence (restart + re-check) -> review & submit activity -> DONE
```

The technician can edit any proposed command, reject it, toggle "auto-approve safe
reads", drop into a **fully interactive terminal** (real PTY — vim/htop/less work),
and **STOP** at any point.

---

## 2. Architecture

```
frontend/                 React + Vite + TS (minimalist SAP-Fiori style)
  src/pages/              TicketList, TicketDetail, Workspace
  src/components/         Terminal (xterm), HypothesisList, ApprovalPrompt,
                          RunControls, ActivityReview
  src/api/client.ts       REST + WebSocket client

backend/app/
  config.py               settings (Phoenix, SSH, OpenRouter dual models)
  models.py               shared schemas + WS event contracts
  erp/client.py           Phoenix ERP client (httpx, auth, timeouts, retries)
  ssh/runner.py           asyncssh command runner (timeouts, cancel)
  safety/rules.py         deterministic deny/confirm/allow + secret redaction
  audit/log.py            append-only JSONL audit log (redaction before persist)
  agent/tools.py          gated tool layer (safety + approval + audit choke point)
  agent/loop.py           the hypothesis-driven state machine
  agent/llm.py            OpenRouter (OpenAI-compatible) client
  agent/activity.py       activity generator (fast model)
  agent/prompts.py        system prompts
  runs/manager.py         run registry + async human-in-the-loop coordination
  api/routes.py, ws.py    REST + WebSocket API
  mock/phoenix.py         offline mock ERP for dev/tests
```

The modules are kept separate on purpose (ERP client, SSH runner, agent, safety
layer, activity generator). The safety gate lives in the orchestrator/tool layer,
and the dangerous-command denylist is also enforced at the lowest level (defense
in depth) — the LLM is never the security boundary.

---

## 2a. Merge origin & adjustments (master-merge)

This branch is the **best-of-both-worlds merge** of two independent rewrites. The
leaner, cleaner `SAP-VERSION` is the structural base (clean module layout, real
`@xterm/xterm` terminal, WebSocket transport, run-manager gates). From the
`feat/ai-service-desk-autopilot` branch we grafted depth: the root-cause-families
doctrine in the hypotheses prompt, the markdown-rich 6-incident mock fixtures, and
the `dev.sh` / `Makefile` one-command runners. (SAP's `decide()/redact()` safety
choke point was kept as-is rather than rewriting it to feat's `classify()` API.)

On top of that base, the requested adjustments:

1. **Interactive terminal (xterm-js).** The xterm pane is now a real PTY on the VM
   (`asyncssh.create_process`), with keystrokes and resize streamed over the
   WebSocket (`terminal.data` / `terminal.resize`) — so **vim, htop, less** work.
   Agent commands still run through the gated `execute_command` choke point and are
   echoed into the same terminal; the interactive PTY is the technician's own
   trusted channel (human input is outside per-command classification by design).
2. **Likelihood as a percentage.** Hypothesis likelihoods are normalised to a
   relative **%** across the ranked set (not a fixed score) and re-ranked by it;
   the UI shows a percentage bar per hypothesis.
3. **No hidden cells.** Every command the agent runs is echoed to the terminal —
   `ssh.run_command` is only ever called from `execute_command`, which emits the
   `$ command` + redacted output as `term.data`. Guarded by a regression test.
4. **Your own hypothesis + comments.** Instead of only picking a ranked hypothesis,
   the technician can **write their own** (title + reasoning + check commands) or
   attach a **comment** to an agent one; comments are fed into the check/re-rank
   context (`submit_hypothesis` / `comment_hypothesis`).
5. **Phase-adaptive right panel.** The full ranked list shows only while choosing
   (HYPOTHESES); during CHECK only the **active** hypothesis remains; during
   fix/validate/etc. the list is hidden.
6. **Multiple checks per hypothesis.** A hypothesis carries a list of check
   commands (not one); the CHECK phase runs and aggregates them all.
7. **Markdown ticket view.** Ticket descriptions render as GitHub-flavoured
   markdown (react-markdown + remark-gfm); the mock seeds markdown reports.

---

## 3. Setup

Requires Docker, or Python 3.11+ and Node 20+ for local dev.

```bash
cp .env.example .env                      # fill in Phoenix URL+token and OpenRouter key
cp /path/to/your-key.pem keys/your-key.pem
# then set SSH_PRIVATE_KEY_PATH=/keys/your-key.pem in .env
```

`.env` and `keys/` are git-ignored. Never commit secrets or keys.

### Environment variables

| Variable | Meaning |
|----------|---------|
| `PHOENIX_API_BASE_URL`, `PHOENIX_API_TOKEN` | The ERP mock and your team token |
| `SSH_PRIVATE_KEY_PATH`, `SSH_USERNAME` | SSH to the customer VM (`azureuser`) |
| `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` | Bring-your-own LLM gateway |
| `AGENT_MODEL` | Strong reasoning model (hypotheses, fix planning) |
| `FAST_MODEL` | Fast model (validation interpretation, activity drafting) |
| `AUTO_APPROVE_READS_DEFAULT` | Default for the per-run "auto-approve safe reads" toggle |
| `VITE_API_BASE` | URL the browser uses to reach the backend |

---

## 4. Run

```bash
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000/health and Swagger at `/docs`

### Run without Docker

```bash
# backend
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload

# frontend (new terminal)
cd frontend && npm install && npm run dev
```

### Develop offline (no Builder Base credentials)

A mock Phoenix ERP is bundled:

```bash
cd backend
.venv/bin/uvicorn app.mock.phoenix:app --port 8009
# in .env: PHOENIX_API_BASE_URL=http://localhost:8009  (any non-empty token works)
```

The mock serves six demo tickets (7001–7006) with markdown reports, per-ticket
customer systems, the activity schema, and the reset endpoint. SSH actions still
need a reachable VM (use a local Ubuntu container or a VM) — without one the run
stops cleanly at the connect step.

> Shortcut: `./dev.sh --mock` (or `make mock`) starts the backend, frontend, and
> mock ERP together; `./dev.sh` / `make dev` runs backend + frontend against your
> real Phoenix from `.env`.

---

## 5. Safety model (category C)

- **Deny-first, deterministic.** `backend/app/safety/rules.py` hard-blocks
  documented hard-fails: recursive deletes / `chmod -R 777` on system paths,
  database drops/truncates, disabling firewall/SELinux/AppArmor/audit, deleting
  logs or shell history, secret-file reads, `dd`/`mkfs` on devices, fork bombs.
  A human cannot override a DENY.
- **Mutations require approval.** Anything that changes state is `CONFIRM` and is
  shown to the technician (editable) before it runs. Safe read-only commands are
  `ALLOW` (auto only when the run's toggle is on; otherwise they also confirm).
- **Secret redaction before persist.** All command output passes through
  `redact()` (PEM blocks, API keys, JWTs, bearer tokens, `password=`/`secret=`
  assignments, connection strings, shadow hashes) before it is logged, streamed
  to the UI, or written into an activity.
- **Audit trail.** Every command and key action is appended to
  `backend/audit_logs/<run_id>.jsonl` with actor, approver, exit code and
  redacted output. This log is also the source for the activity generator.
- **Human control.** Mandatory approval to connect and for every mutation; STOP
  unblocks any pending wait and cancels in-flight work.

---

## 6. Tests

```bash
cd backend && .venv/bin/python -m pytest
```

- `test_safety.py` — hard-fail denylist, mutation gating, reads, redaction.
- `test_erp_client.py` — Phoenix client against the in-process mock (auth/404/activity).
- `test_run_manager.py` — approval/stop/hypothesis selection, custom hypothesis, comments.
- `test_agent_loop.py` — full run end-to-end with fakes (the walking skeleton).
- `test_adjustments.py` — likelihood %, multi-check, own-hypothesis, no-hidden-cells,
  and the interactive PTY shell wiring.

Or `make test`. **81 tests pass offline** (no credentials needed).

---

## 7. Assumptions

- Customer VMs are Ubuntu with systemd; the approach aims to be OS-agnostic but
  assumes `bash`, `systemctl`, `journalctl`, etc. exist.
- Host-key checking is disabled for the provided hackathon VMs.
- The LLM is reached via OpenRouter (OpenAI-compatible). Without an
  `OPENROUTER_API_KEY` the app still runs: it performs recon and lets the
  technician drive the terminal manually.
- One run per ticket at a time; runs are kept in memory (no DB).

---

## 8. Troubleshooting

- **401 from Phoenix** → check `PHOENIX_API_TOKEN` / `Authorization: Bearer`.
- **Empty ticket list** → confirm the token and `GET /api/tickets` work; try the mock.
- **SSH connect fails** → key at `SSH_PRIVATE_KEY_PATH`, user `azureuser`, VM
  reachable from where the backend runs; a connect timeout is enforced.
- **LLM errors / no hypotheses** → check `OPENROUTER_API_KEY`, `AGENT_MODEL`,
  `FAST_MODEL`; the run falls back to manual recon if no key is set.
- **Can't reach a host-run mock from Docker** → use `host.docker.internal`.
- **Terminal output looks doubled** → the dev server is single-connection by
  design (StrictMode is disabled); hard-refresh if you reconnected mid-run.

---

MIT licensed (see `LICENSE`). See `docs/phoenix-openapi.yaml` for the ERP contract
and `docs/scoring.md` for the rubric.
