"""Gated tool layer.

Every command the agent (or the technician) runs goes through ``execute_command``,
which is the single choke point for: deterministic safety classification, the
human approval gate, secret redaction, audit logging and UI streaming. The agent
loop never talks to SSH directly.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from app.models import EventType
from app.safety import decide, redact

if TYPE_CHECKING:
    from app.runs.manager import Run


@dataclass
class ExecResult:
    command: str
    exit_code: Optional[int]
    output: str  # already redacted; safe to show the LLM, UI and logs
    blocked: bool = False
    rejected: bool = False
    timed_out: bool = False
    reason: str = ""
    # The command the agent originally requested, set only when the technician
    # edited it before approving. Lets the loop tell the agent its command changed.
    edited_from: Optional[str] = None

    @property
    def ok(self) -> bool:
        return (
            self.exit_code == 0
            and not self.blocked
            and not self.rejected
            and not self.timed_out
        )


# Deterministic recon: cheap, read-only evidence gathering before hypotheses.
RECON_PLAYBOOK: list[tuple[str, str]] = [
    ("OS / kernel", "cat /etc/os-release 2>/dev/null; uname -a"),
    ("Failed systemd units", "systemctl --failed --no-legend --no-pager 2>/dev/null"),
    ("Recent unit errors", "journalctl -p err -b -n 60 --no-pager 2>/dev/null"),
    ("Disk usage", "df -h"),
    ("Inode usage", "df -i"),
    ("Memory", "free -m"),
    ("Listening sockets", "ss -tulpn 2>/dev/null || ss -tuln"),
    ("Top processes", "ps aux --sort=-%cpu | head -n 12"),
]


async def execute_command(
    run: "Run",
    command: str,
    actor: str = "agent",
    purpose: str = "",
    require_confirm: bool = True,
    original_command: Optional[str] = None,
) -> ExecResult:
    """Run a command through the safety + approval + audit pipeline.

    ``require_confirm=False`` skips the per-command approval prompt (used when a
    batch of commands was already approved, e.g. an approved fix plan). The DENY
    safety check ALWAYS applies regardless of this flag.

    ``original_command`` is what the agent originally asked for, when the command
    was already edited by the technician UPSTREAM (e.g. an edited fix plan, which
    bypasses the per-command gate). It lets the UI/agent learn the command changed
    even though no gate edit happened here. The per-command gate sets this itself.
    """
    run.check_stop()
    command = command.strip()
    if not command:
        return ExecResult(command, None, "", reason="empty command")

    decision = decide(command, run.auto_approve_reads)

    if decision.action == "DENY":
        return _blocked(run, command, actor, decision.reason)

    # What the agent asked for, before any human edit. An upstream edit (fix plan)
    # passes the pre-edit text in; the per-command gate edit is detected below.
    requested_command = (original_command or command).strip()
    approver = actor
    if decision.action == "CONFIRM" and actor == "agent" and require_confirm:
        dec = await run.request_approval(
            "command", {"command": command, "reason": decision.reason}, purpose
        )
        if not dec.approved:
            reject_reason = (dec.reason or "").strip()
            note = "[rejected by technician" + (f": {reject_reason}" if reject_reason else "") + "]"
            run.audit.record(
                "command", command=command, actor=actor, approved=False,
                rejected=True, reason=reject_reason, exit_code=None,
            )
            run.emit(
                EventType.COMMAND, command=command, actor=actor, rejected=True,
                reason=reject_reason, exit_code=None, output_redacted=note,
            )
            return ExecResult(command, None, note, rejected=True, reason=reject_reason)
        if dec.edited:
            command = dec.edited.strip()
            # Re-classify the edited command; a human can never push it past DENY.
            if decide(command, run.auto_approve_reads).action == "DENY":
                return _blocked(run, command, actor, "edited command is unsafe")
        approver = "technician"

    # Non-empty only when the technician changed the agent's command at the gate.
    edited_from = requested_command if command != requested_command else None

    if run.ssh is None or not run.ssh.connected:
        return ExecResult(
            command, None, "[no SSH connection]", reason="not connected", edited_from=edited_from
        )

    run.emit(EventType.TERM, data=f"$ {command}\r\n")
    result = await run.ssh.run_command(command)
    redacted_output, _ = redact(result.combined)

    run.audit.record(
        "command",
        command=command,
        actor=actor,
        approver=approver,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        output=result.combined,  # AuditLog redacts before persist
        purpose=purpose,
        edited_from=edited_from,
    )
    run.emit(
        EventType.COMMAND,
        command=command,
        actor=actor,
        approver=approver,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        output_redacted=redacted_output,
        purpose=purpose,
        edited_from=edited_from,
    )
    if redacted_output:
        run.emit(EventType.TERM, data=redacted_output.replace("\n", "\r\n") + "\r\n")

    return ExecResult(
        command=command,
        exit_code=result.exit_code,
        output=redacted_output,
        timed_out=result.timed_out,
        edited_from=edited_from,
    )


def _blocked(run: "Run", command: str, actor: str, reason: str) -> ExecResult:
    run.audit.record(
        "command", command=command, actor=actor, blocked=True, reason=reason, exit_code=None
    )
    run.emit(
        EventType.COMMAND,
        command=command,
        actor=actor,
        blocked=True,
        reason=reason,
        exit_code=None,
        output_redacted=f"[BLOCKED by safety layer: {reason}]",
    )
    run.emit(EventType.TERM, data=f"$ {command}\r\n[BLOCKED: {reason}]\r\n")
    return ExecResult(command, None, f"[BLOCKED: {reason}]", blocked=True, reason=reason)


def quote_path(path: str) -> str:
    return shlex.quote(path)
