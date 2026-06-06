"""Activity generator — turns the audit transcript into the ERP activity fields.

Uses the fast model. Falls back to a deterministic draft from the audit log when
no LLM is configured, so the workflow still completes offline.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent import prompts
from app.agent.llm import LLM, LLMError
from app.agent.schemas import ActivityOutput

if TYPE_CHECKING:
    from app.runs.manager import Run


async def draft_activity(run: "Run", llm: LLM) -> dict[str, Any]:
    transcript = run.audit.as_transcript()
    ticket = run.ticket
    user = (
        f"Ticket #{getattr(ticket, 'id', run.ticket_id)}: {getattr(ticket, 'title', '')}\n"
        f"Customer report (symptom): {getattr(ticket, 'description', '')}\n\n"
        f"Run transcript (redacted):\n{transcript}"
    )
    if llm.configured:
        try:
            data = await llm.complete_json(
                prompts.ACTIVITY_SYSTEM, user, model=llm.fast_model, schema=ActivityOutput
            )
            return _normalise(data)
        except LLMError:
            pass
    return _fallback(run)


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    fields = ("summary", "root_cause", "actions_taken", "commands_summary",
              "validation_result", "description")
    return {f: str(data.get(f, "") or "") for f in fields}


def _fallback(run: "Run") -> dict[str, Any]:
    commands = run.audit.commands()
    cmd_lines = [c.get("command", "") for c in commands if not c.get("blocked")]
    validations = [e for e in run.audit.entries if e.get("type") == "validation"]
    validation_text = validations[-1].get("text", "") if validations else "Manual verification pending."
    title = getattr(run.ticket, "title", f"ticket {run.ticket_id}")
    return {
        "summary": f"Investigated and addressed: {title}.",
        "root_cause": "See actions taken (drafted without LLM).",
        "actions_taken": "; ".join(cmd_lines[:20]) or "Diagnostic commands executed over SSH.",
        "commands_summary": "; ".join(sorted({c.split()[0] for c in cmd_lines if c}))[:500],
        "validation_result": validation_text,
        "description": f"Worked ticket #{run.ticket_id} ({title}) via the AI Service Desk Autopilot.",
    }
