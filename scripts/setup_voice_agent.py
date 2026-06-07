#!/usr/bin/env python3
"""Provision a fully-configured ElevenLabs voice agent for run steering.

Creates (or reuses) the 11 client tools the browser registers, then creates (or
updates) an Agent wired to those tools with our system prompt + first message —
so the agent does EXACTLY what this app needs instead of the default template.

Why a script: ElevenLabs now manages tools as first-class objects referenced by
``tool_ids`` (the inline ``prompt.tools`` field was removed in mid-2025), and the
agent's prompt/first-message live server-side. This makes that setup reproducible.

Usage (from the repo root; stdlib only, no pip install needed):

    python3 scripts/setup_voice_agent.py

It reads ``ELEVENLABS_API_KEY`` (required) and optionally ``ELEVENLABS_AGENT_ID``
(update that agent instead of creating a new one), ``ELEVENLABS_BASE_URL`` and
``ELEVENLABS_VOICE_ID`` from ``.env`` in the repo root or the real environment.
It prints the agent id to paste into ``.env`` as ``ELEVENLABS_AGENT_ID``.

The API key needs the *Conversational AI* (Agents) **read & write** permission.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://api.elevenlabs.io"
# A standard ElevenLabs prebuilt voice ("Rachel") available to every account, so
# the script works without first cloning/selecting a voice. Override via env.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

AGENT_NAME = "Service Desk Voice Copilot"

SYSTEM_PROMPT = """You are a calm, efficient co-pilot voice assistant for a Linux service-desk technician running an autonomous AI troubleshooting session ("the run"). The run is an AI agent that diagnoses and fixes a customer ticket over SSH, pausing at human-approval gates.

Your job is to let the technician steer that run hands-free, by voice.

How you work:
- You are NOT the troubleshooting agent. You are the voice layer that reads the run's state and relays the technician's decisions into it, using your tools.
- Whenever you are unsure what is happening, call get_run_status first and tell the technician where the run stands (phase, any pending approval or decision, available hypotheses).
- When you receive a contextual update (e.g. "Approval required" or "Decision required"), proactively tell the technician and offer to act.

Steering tools:
- approve_action / reject_action: respond to a pending command-approval gate. SAFETY: before calling approve_action, read the exact pending command back to the technician and get explicit verbal confirmation. For a destructive/mutating command, be extra careful and confirm twice.
- select_hypothesis: pick a ranked hypothesis by its number or title.
- comment_hypothesis: attach a steering comment to a hypothesis.
- submit_hypothesis: propose the technician's own hypothesis.
- answer_decision: choose one of the options the run is asking about.
- set_auto_approve_reads: toggle auto-approval of safe read-only commands.
- run_command: run a one-shot command through the run's safety gate (it may still require approval; it can never bypass the deny-list).
- submit_activity: submit the final activity report when a draft is ready.
- stop_run: immediately stop the run. Confirm verbally first.

Rules:
- Never invent terminal output, command results, hypotheses, or status. Only state what the tools return to you.
- Keep replies short, calm, and spoken-friendly. Confirm what you did after each action.
- You cannot override a hard-denied command; if the run blocks something, explain that the safety layer denied it.""".strip()

FIRST_MESSAGE = (
    "Voice control connected. I can read the run's status and steer it for you — "
    "say 'status' any time to hear where things stand."
)

# Each entry mirrors a client tool registered in frontend/src/voice/useVoiceControl.ts.
# Parameter types are kept to primitives (string/boolean) for maximum compatibility;
# the frontend wrapper splits `checks` (a delimited string) into a list.
TOOLS: list[dict] = [
    {
        "name": "get_run_status",
        "description": "Read the current state of the troubleshooting run: phase, any pending approval or decision, the available hypotheses, and whether an activity draft is ready. Call this whenever you are unsure what is happening.",
        "properties": {},
        "required": [],
    },
    {
        "name": "approve_action",
        "description": "Approve the command the run is currently waiting on at an approval gate. Optionally provide an edited command to run instead. Only call after the technician has verbally confirmed.",
        "properties": {
            "edited_command": {
                "type": "string",
                "description": "Optional replacement command to approve instead of the proposed one. Omit to approve the command as-is.",
            }
        },
        "required": [],
    },
    {
        "name": "reject_action",
        "description": "Reject the command the run is currently waiting on at an approval gate.",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Optional short reason for rejecting (informational).",
            }
        },
        "required": [],
    },
    {
        "name": "select_hypothesis",
        "description": "Select one of the run's ranked hypotheses to investigate, by its rank number (e.g. '1') or a distinctive part of its title.",
        "properties": {
            "hypothesis": {
                "type": "string",
                "description": "The hypothesis rank number or a substring of its title.",
            }
        },
        "required": ["hypothesis"],
    },
    {
        "name": "comment_hypothesis",
        "description": "Attach a steering comment to one of the run's hypotheses (identified by rank number or title substring).",
        "properties": {
            "hypothesis": {
                "type": "string",
                "description": "The hypothesis rank number or a substring of its title.",
            },
            "text": {
                "type": "string",
                "description": "The comment to attach.",
            },
        },
        "required": ["hypothesis", "text"],
    },
    {
        "name": "submit_hypothesis",
        "description": "Propose the technician's own hypothesis instead of picking a ranked one.",
        "properties": {
            "title": {"type": "string", "description": "Short title of the hypothesis."},
            "reasoning": {
                "type": "string",
                "description": "Why this might be the root cause.",
            },
            "checks": {
                "type": "string",
                "description": "Optional read-only check command(s) to verify it; separate multiple with semicolons.",
            },
        },
        "required": ["title", "reasoning"],
    },
    {
        "name": "answer_decision",
        "description": "Answer the question the run is currently asking by choosing one of its offered options (match by the option text).",
        "properties": {
            "choice": {
                "type": "string",
                "description": "The chosen option (exact text or a distinctive substring of it).",
            }
        },
        "required": ["choice"],
    },
    {
        "name": "set_auto_approve_reads",
        "description": "Turn auto-approval of safe, read-only commands on or off for this run.",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "True to auto-approve safe reads, false to require approval for every command.",
            }
        },
        "required": ["enabled"],
    },
    {
        "name": "run_command",
        "description": "Run a single command on the customer VM through the run's safety gate. It may still require approval and can never bypass the deny-list.",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run.",
            }
        },
        "required": ["command"],
    },
    {
        "name": "submit_activity",
        "description": "Submit the run's final activity report (the resolution write-up) when a draft is ready for review.",
        "properties": {},
        "required": [],
    },
    {
        "name": "stop_run",
        "description": "Immediately stop the run. Confirm with the technician verbally first.",
        "properties": {},
        "required": [],
    },
]


# --------------------------------------------------------------------------- #
# .env loading + HTTP (stdlib only)
# --------------------------------------------------------------------------- #
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    # Real environment wins over the file.
    for key in (
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_AGENT_ID",
        "ELEVENLABS_BASE_URL",
        "ELEVENLABS_VOICE_ID",
    ):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def api(method: str, base: str, path: str, key: str, body: dict | None = None) -> dict:
    url = f"{base}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("xi-api-key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:800]
        raise SystemExit(f"\n✗ {method} {path} -> HTTP {exc.code}\n  {detail}\n")
    except urllib.error.URLError as exc:
        raise SystemExit(f"\n✗ {method} {path} -> network error: {exc}\n")


def tool_config(spec: dict) -> dict:
    return {
        "type": "client",
        "name": spec["name"],
        "description": spec["description"],
        "response_timeout_secs": 15,
        "expects_response": True,  # the agent uses each tool's returned string
        "parameters": {
            "type": "object",
            "properties": spec["properties"],
            "required": spec["required"],
        },
    }


# --------------------------------------------------------------------------- #
def main() -> None:
    env = load_env()
    key = env.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is not set (in .env or the environment).")
    base = (env.get("ELEVENLABS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    voice_id = env.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID
    # `--new` forces creating a fresh, dedicated agent even if ELEVENLABS_AGENT_ID
    # is already set (which would otherwise UPDATE that agent in place).
    force_new = "--new" in sys.argv
    existing_agent_id = "" if force_new else env.get("ELEVENLABS_AGENT_ID", "").strip()

    print(f"Using ElevenLabs at {base}")

    # 1. Reuse client tools by name where they already exist; create the rest.
    print("\nResolving client tools…")
    listed = api("GET", base, "/v1/convai/tools", key)
    by_name: dict[str, str] = {}
    for tool in listed.get("tools", []):
        name = (tool.get("tool_config") or {}).get("name")
        if name:
            by_name[name] = tool["id"]

    tool_ids: list[str] = []
    for spec in TOOLS:
        name = spec["name"]
        if name in by_name:
            tool_ids.append(by_name[name])
            print(f"  • reuse  {name}  ({by_name[name]})")
        else:
            created = api("POST", base, "/v1/convai/tools", key, {"tool_config": tool_config(spec)})
            tid = created["id"]
            tool_ids.append(tid)
            print(f"  • create {name}  ({tid})")

    # 2. Create or update the agent wired to those tools.
    conversation_config = {
        "agent": {
            "prompt": {"prompt": SYSTEM_PROMPT, "tool_ids": tool_ids},
            "first_message": FIRST_MESSAGE,
            "language": "en",
        },
        "tts": {"voice_id": voice_id},
    }
    body = {"name": AGENT_NAME, "conversation_config": conversation_config}

    if existing_agent_id:
        print(f"\nUpdating existing agent {existing_agent_id}…")
        api("PATCH", base, f"/v1/convai/agents/{existing_agent_id}", key, body)
        agent_id = existing_agent_id
    else:
        print("\nCreating a new agent…")
        created = api("POST", base, "/v1/convai/agents/create", key, body)
        agent_id = created["agent_id"]

    print("\n✓ Done.")
    print(f"\n  Agent: {AGENT_NAME}")
    print(f"  Tools wired: {len(tool_ids)}")
    print("\nPut this in your .env (then restart the backend):\n")
    print(f"  ELEVENLABS_AGENT_ID={agent_id}\n")
    if not existing_agent_id:
        print("Tip: keep ELEVENLABS_AGENT_ID set and re-run this script to UPDATE the")
        print("same agent (prompt + tools) instead of creating duplicates.\n")


if __name__ == "__main__":
    sys.exit(main())
