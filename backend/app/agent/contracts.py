"""Agent model interaction contract — SPEC §7.2.

Defines:
  - AGENT_TOOL_NAME / AGENT_RESPONSE_SCHEMA  — the Anthropic tool / OpenAI
    function schema used to force structured output from the LLM.
  - build_user_message()  — assembles the per-call user message.
  - parse_agent_response() — validates raw model output → AgentResponse.
  - history_to_contract_dicts() — maps AuditEntry list to the prompt shape.
  - MalformedAgentResponse — raised on invalid model output (triggers reprompt).
"""
from __future__ import annotations

from typing import Any

from app.models import AgentResponse, AuditEntry, Customer, CustomerSystem, Ticket

# ---------------------------------------------------------------------------
# Tool / function name exposed to the LLM
# ---------------------------------------------------------------------------

AGENT_TOOL_NAME: str = "report"

# ---------------------------------------------------------------------------
# JSON Schema — the model MUST return data matching this shape exactly.
# Used as `input_schema` for Anthropic tools / `parameters` for OpenAI functions.
# ---------------------------------------------------------------------------

AGENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "phase",
        "thought",
        "hypotheses",
        "proposed_commands",
        "ready_to_validate",
        "ready_to_finish",
        "final",
    ],
    "additionalProperties": False,
    "properties": {
        "phase": {
            "type": "string",
            "description": (
                "Current orchestrator phase, e.g. 'diagnose', 'propose_fix', "
                "'validate', 'persist_check'."
            ),
        },
        "thought": {
            "type": "string",
            "description": "Short chain-of-thought reasoning for this response.",
        },
        "hypotheses": {
            "type": "array",
            "description": "Ranked hypotheses about the root cause.",
            "items": {
                "type": "object",
                "required": ["cause", "evidence", "confidence"],
                "additionalProperties": False,
                "properties": {
                    "cause": {
                        "type": "string",
                        "description": "Description of the hypothesised root cause.",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Evidence from command output supporting this hypothesis.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence score 0-1.",
                    },
                },
            },
        },
        "proposed_commands": {
            "type": "array",
            "description": "Commands proposed for execution in this phase.",
            "items": {
                "type": "object",
                "required": [
                    "command",
                    "purpose",
                    "mutating",
                    "expected_effect",
                    "rollback",
                ],
                "additionalProperties": False,
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to run.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Why this command is being proposed.",
                    },
                    "mutating": {
                        "type": "boolean",
                        "description": (
                            "True if the command changes any system state "
                            "(writes, creates, removes, enables, disables, restarts, "
                            "or otherwise modifies). Must be set truthfully."
                        ),
                    },
                    "expected_effect": {
                        "type": "string",
                        "description": "What the command is expected to produce or change.",
                    },
                    "rollback": {
                        "type": "string",
                        "description": (
                            "How to undo this command if needed. "
                            "Empty string is acceptable for read-only commands."
                        ),
                    },
                },
            },
        },
        "ready_to_validate": {
            "type": "boolean",
            "description": (
                "Set true when diagnosis is complete and the fix has been applied — "
                "the orchestrator will advance to VALIDATE."
            ),
        },
        "ready_to_finish": {
            "type": "boolean",
            "description": (
                "Set true when the fix is validated and persistent. "
                "Must be accompanied by a populated 'final' object."
            ),
        },
        "final": {
            "description": (
                "Required when ready_to_finish is true; null otherwise. "
                "Drafts the ERP activity fields for human review."
            ),
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "required": [
                        "root_cause",
                        "summary",
                        "actions_taken",
                        "commands_summary",
                        "validation_result",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "root_cause": {
                            "type": "string",
                            "description": (
                                "The technical root cause — not the symptom. "
                                "E.g. 'nginx unit not enabled after package reconfigure'."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": "One-sentence summary of what was restored.",
                        },
                        "actions_taken": {
                            "type": "string",
                            "description": "Ordered diagnosis and fix steps.",
                        },
                        "commands_summary": {
                            "type": "string",
                            "description": (
                                "Relevant commands or command classes used — no secrets."
                            ),
                        },
                        "validation_result": {
                            "type": "string",
                            "description": (
                                "Concrete proof the customer benefit is restored "
                                "(e.g. HTTP 200, port listening, unit active+enabled)."
                            ),
                        },
                    },
                },
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MalformedAgentResponse(Exception):
    """Raised when the model returns output that does not match the schema.

    The detail message is suitable for inclusion in a reprompt to the model
    so it can self-correct.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------------------
# History helper
# ---------------------------------------------------------------------------

_MAX_OUTPUT_HEAD = 60  # lines kept from the start of a long output
_MAX_OUTPUT_TAIL = 20  # lines kept from the end of a long output


def _truncate_output(text: str) -> str:
    """Keep head + tail of long outputs to bound prompt size."""
    lines = text.splitlines()
    if len(lines) <= _MAX_OUTPUT_HEAD + _MAX_OUTPUT_TAIL:
        return text
    head = lines[:_MAX_OUTPUT_HEAD]
    tail = lines[-_MAX_OUTPUT_TAIL:]
    omitted = len(lines) - _MAX_OUTPUT_HEAD - _MAX_OUTPUT_TAIL
    return "\n".join(head) + f"\n... [{omitted} lines omitted] ...\n" + "\n".join(tail)


def history_to_contract_dicts(entries: list[AuditEntry]) -> list[dict[str, Any]]:
    """Map a list of AuditEntry objects to the prompt history shape.

    Each dict has: command, classification, approval, exit_code, redacted_output.
    """
    result: list[dict[str, Any]] = []
    for entry in entries:
        result.append(
            {
                "command": entry.command,
                "classification": entry.classification,
                "approval": entry.approval,
                "exit_code": entry.exit_code,
                "redacted_output": _truncate_output(entry.output_summary),
            }
        )
    return result


# ---------------------------------------------------------------------------
# User message builder
# ---------------------------------------------------------------------------

_PHASE_GUIDANCE: dict[str, str] = {
    "triage": (
        "You are in the TRIAGE phase. Review the ticket and customer-system info. "
        "Identify the affected service/capability and the expected healthy state. "
        "Propose the first batch of read-only diagnostic commands."
    ),
    "diagnose": (
        "You are in the DIAGNOSE phase. Propose read-only diagnostic commands to "
        "gather evidence. All commands must have `mutating: false`. After reviewing "
        "the results, update your ranked hypotheses. When you have a confident root "
        "cause, set `ready_to_validate: true` and move toward PROPOSE_FIX."
    ),
    "propose_fix": (
        "You are in the PROPOSE_FIX phase. Based on your ranked hypotheses, propose "
        "the ordered list of mutating commands that will fix the root cause. Each "
        "command must have `mutating: true`, a clear purpose, expected effect, and "
        "rollback. Fixes must be persistent (enable services, edit on-disk config, "
        "add mounts to /etc/fstab). Keep changes minimal."
    ),
    "apply": (
        "You are in the APPLY phase. Commands are being executed one by one under "
        "human supervision. Review the result of each executed command and advise "
        "on next steps. If a command failed, propose a corrective action."
    ),
    "validate": (
        "You are in the VALIDATE phase. Propose concrete read-only validation "
        "commands that prove the customer's benefit is restored: HTTP 200, port "
        "listening, unit active+enabled, file present with correct perms, query "
        "succeeding. Set `ready_to_finish: true` only when validation passes."
    ),
    "persist_check": (
        "You are in the PERSIST_CHECK phase. Verify the fix is durable: check "
        "`systemctl is-enabled`, config/unit files on disk, fstab entries. "
        "Restart the affected service (not the VM) and re-validate. Only set "
        "`ready_to_finish: true` after confirming the fix survives a restart."
    ),
    "document": (
        "You are in the DOCUMENT phase. Provide `final` with a technical root_cause "
        "(not the symptom), ordered actions_taken, secret-free commands_summary, "
        "and concrete validation_result. Set `ready_to_finish: true`."
    ),
}


def build_user_message(
    *,
    ticket: Ticket,
    customer_system: CustomerSystem | None,
    customer: Customer | None,
    phase: str,
    history: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    extra_note: str | None = None,
) -> str:
    """Build the user message sent to the LLM for one agent step.

    Assembles ticket info, customer-system (already redacted by the caller),
    phase guidance, full ordered command history, and current hypotheses into
    a compact, readable Markdown string.
    """
    sections: list[str] = []

    # --- Ticket ---
    sections.append("## Ticket")
    sections.append(f"- **ID:** {ticket.id}")
    sections.append(f"- **Title:** {ticket.title}")
    sections.append(f"- **Priority:** {ticket.priority}")
    sections.append(f"- **Status:** {ticket.status.value}")
    if ticket.customer_name:
        sections.append(f"- **Customer:** {ticket.customer_name}")
    sections.append(f"\n**Description:**\n{ticket.description.strip()}")

    # --- Customer system ---
    sections.append("\n## Customer System (secrets already redacted)")
    if customer_system is not None:
        sys_info = customer_system.system
        sections.append(f"- **IP:** {sys_info.ip}")
        sections.append(f"- **Port:** {sys_info.port}")
        sections.append(f"- **SSH User:** {sys_info.username}")
        sections.append(f"- **OS:** {sys_info.os}")
        if sys_info.notes:
            sections.append(f"- **Notes:** {sys_info.notes}")
    else:
        sections.append("_(customer-system not available)_")

    # --- Customer ---
    if customer is not None:
        sections.append("\n## Customer")
        sections.append(f"- **Company:** {customer.company_name}")
        sections.append(
            f"- **Contact:** {customer.firstname} {customer.lastname}"
        )

    # --- Phase guidance ---
    sections.append(f"\n## Current Phase: {phase.upper()}")
    guidance = _PHASE_GUIDANCE.get(phase.lower(), "Proceed according to the doctrine.")
    sections.append(guidance)

    # --- Extra note (reprompt / correction) ---
    if extra_note:
        sections.append(f"\n## Important Note\n{extra_note}")

    # --- Command history ---
    sections.append("\n## Command History (ordered, outputs already redacted)")
    if history:
        for i, entry in enumerate(history, 1):
            cmd = entry.get("command", "")
            classification = entry.get("classification", "")
            approval = entry.get("approval", "")
            exit_code = entry.get("exit_code")
            output = entry.get("redacted_output", "")
            exit_str = str(exit_code) if exit_code is not None else "N/A"
            sections.append(
                f"\n### [{i}] `{cmd}`\n"
                f"- Classification: {classification} | Approval: {approval} | Exit: {exit_str}\n"
                f"- Output:\n```\n{output}\n```"
            )
    else:
        sections.append("_(no commands executed yet)_")

    # --- Current hypotheses ---
    sections.append("\n## Current Hypotheses")
    if hypotheses:
        for h in hypotheses:
            cause = h.get("cause", "")
            evidence = h.get("evidence", "")
            confidence = h.get("confidence", 0.0)
            sections.append(
                f"- **{cause}** (confidence: {confidence:.0%})\n  Evidence: {evidence}"
            )
    else:
        sections.append("_(none yet — gather evidence first)_")

    sections.append(
        "\n---\nRespond by calling the `report` tool with the JSON object matching "
        "the schema. Propose ONE phase's worth of commands."
    )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def parse_agent_response(raw: dict[str, Any] | str) -> AgentResponse:
    """Validate and parse raw model output into an AgentResponse.

    Accepts either a dict (from the tool_use input block) or a JSON string.
    Raises MalformedAgentResponse with a descriptive message on any failure
    so the caller can include it in a corrective reprompt.
    """
    import json  # standard library — kept inline to match lazy-import pattern

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedAgentResponse(
                f"Response is not valid JSON: {exc}"
            ) from exc

    if not isinstance(raw, dict):
        raise MalformedAgentResponse(
            f"Expected a JSON object, got {type(raw).__name__}."
        )

    # Validate required top-level keys
    required = {
        "phase",
        "thought",
        "hypotheses",
        "proposed_commands",
        "ready_to_validate",
        "ready_to_finish",
        "final",
    }
    missing = required - raw.keys()
    if missing:
        raise MalformedAgentResponse(
            f"Response missing required keys: {sorted(missing)}. "
            "Call the `report` tool with ALL required fields."
        )

    try:
        return AgentResponse.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError or similar
        raise MalformedAgentResponse(
            f"Response failed schema validation: {exc}. "
            "Ensure all fields match the `report` tool schema exactly."
        ) from exc
