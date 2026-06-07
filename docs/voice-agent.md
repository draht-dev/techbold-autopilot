# ElevenLabs Voice Agent Setup

This document explains how to configure the ElevenLabs Conversational AI agent so that it matches the client tools registered in the frontend.

---

## Environment variables

| Variable | Where | Description |
|---|---|---|
| `ELEVENLABS_API_KEY` | Backend | Your ElevenLabs API key. Used by the backend to generate signed WebSocket URLs. |
| `ELEVENLABS_AGENT_ID` | Backend | The ID of the agent you created in the ElevenLabs dashboard. Returned by `GET /api/voice/config`. |

When either variable is missing, `GET /api/voice/config` returns `{ "enabled": false }` and `GET /api/voice/signed-url` returns 503. The frontend degrades gracefully: the voice panel shows an error on connect but the rest of the workspace is unaffected.

---

## Creating the agent in the ElevenLabs dashboard

1. Go to **https://elevenlabs.io** → Conversational AI → Agents → **Create agent**.
2. Give it a name (e.g. "Service Desk Autopilot").
3. In **Security settings**, enable **"Allow client-side overrides"** — this lets the frontend send a runtime system prompt and first message via the `overrides` field in the session config. Without this the hardcoded agent prompt takes precedence and the voice agent won't know about the run context.
4. Choose a voice that sounds calm and clear (e.g. Rachel).

### System prompt

Paste this into the agent's **System prompt** field (the frontend also overrides it at runtime via `overrides.agent.prompt`, so this acts as a fallback):

```
You are a calm, efficient co-pilot voice assistant for a Linux service-desk technician running an autonomous AI troubleshooting session.
You can read the current run state at any time using the get_run_status tool.
You can steer the run using the other available tools: approve/reject commands, pick or comment on hypotheses, answer decision prompts, toggle auto-approve, send commands, submit the activity report, or stop the run.
Rules:
- Always call get_run_status first if you are unsure what is happening.
- For approve_action: read the pending command back to the technician and ask for explicit verbal confirmation before approving — this is a safety gate.
- Never invent terminal output or results; only report what the tools return.
- Keep responses concise and actionable.
- When you receive a contextual update about a pending approval or decision, proactively inform the technician and offer to help.
```

### First message

```
Voice control connected. Say 'status' any time to hear where the run stands.
```

---

## Client tools

Add each tool below in the ElevenLabs dashboard under **Tools → Client tools**. The name and parameter names must match exactly — the frontend registers them under these identifiers.

**Important**: tool names use underscores, not hyphens.

---

### `get_run_status`

**Description**: Returns a human-readable summary of the current phase, any pending approval or decision (with command/question text), the ranked hypotheses list, and whether an activity draft is ready. Call this whenever you need to orient yourself.

**Parameters**: none

---

### `approve_action`

**Description**: Approves the currently pending command. Optionally supply an edited version of the command text (for command-kind approvals). Always read the command back to the technician and confirm verbally before calling this tool — approvals are irreversible.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `edited_command` | string | No | Modified command text to approve instead of the original. |

---

### `reject_action`

**Description**: Rejects the currently pending command/fix. The agent will not run it.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `reason` | string | No | Short reason for the rejection (informational, logged to context). |

---

### `select_hypothesis`

**Description**: Select a hypothesis for the agent to check. The technician identifies the hypothesis by its rank number (e.g. "1") or a substring of the title.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `hypothesis` | string | Yes | Rank number (e.g. "2") or title substring (e.g. "disk space"). |

---

### `comment_hypothesis`

**Description**: Add a steering comment to a hypothesis so the agent incorporates the technician's knowledge into its investigation.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `hypothesis` | string | Yes | Rank number or title substring identifying the hypothesis. |
| `text` | string | Yes | The comment text to attach. |

---

### `submit_hypothesis`

**Description**: Propose a brand-new hypothesis written by the technician. The agent will queue it alongside the AI-generated ones.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `title` | string | Yes | One-line root-cause statement. |
| `reasoning` | string | No | Why you think this is the cause. |
| `checks` | array of string | No | Read-only shell commands that would confirm or rule out the hypothesis. |

---

### `answer_decision`

**Description**: Pick one of the choices when the agent is blocked on a decision prompt (e.g. "reproduce then fix" vs "skip reproduction"). Match is case-insensitive; a substring of the option text is sufficient.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `choice` | string | Yes | The option to select (substring match, e.g. "skip"). |

---

### `set_auto_approve_reads`

**Description**: Toggle whether safe read-only commands (e.g. `cat`, `ls`, `ps`) are auto-approved without requiring human confirmation.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | Yes | `true` to turn auto-approve on, `false` to require manual approval for every command. |

---

### `run_command`

**Description**: Send a one-shot shell command through the normal safety gate (the same path as terminal input). The command will surface as an approval request if it is flagged as a mutation. Do not use this to bypass safety.

**Parameters**:

| Name | Type | Required | Description |
|---|---|---|---|
| `command` | string | Yes | The shell command to execute (e.g. `systemctl status nginx`). |

---

### `submit_activity`

**Description**: Submit the current activity draft (summary, root cause, actions taken) to close the ticket. Only works when an activity draft is available.

**Parameters**: none

---

### `stop_run`

**Description**: Stop the troubleshooting run immediately. The SSH connection is released and the ticket remains open for manual follow-up. Ask the technician to confirm before calling.

**Parameters**: none

---

## How the signed URL flow works

1. The frontend calls `GET /api/voice/signed-url` (backend uses `ELEVENLABS_API_KEY` + `ELEVENLABS_AGENT_ID` to generate a short-lived `wss://` URL).
2. The frontend opens a WebSocket to that URL via `startSession({ signedUrl, connectionType: "websocket" })`.
3. The ElevenLabs server authenticates the session using the signed URL — no API key is ever sent to the browser.
4. The `overrides` field in the session config is accepted only if the agent has "Allow client-side overrides" enabled (step 3 above).

## Proactive notifications

The frontend pushes contextual updates to the voice agent (via `sendContextualUpdate`) when:
- An approval is requested — the agent is expected to read out the command and ask for confirmation.
- A decision is needed — the agent reads out the question and options.
- Hypotheses arrive — the agent reads out the top entries.
- An activity draft is ready — the agent prompts the technician to review and submit.
- The run phase changes — the agent narrates the milestone.

These are one-way pushes; the agent decides whether and how to speak them based on its system prompt.
