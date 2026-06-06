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
) -> ExecResult:
    """Run a command through the safety + approval + audit pipeline.

    ``require_confirm=False`` skips the per-command approval prompt (used when a
    batch of commands was already approved, e.g. an approved fix plan). The DENY
    safety check ALWAYS applies regardless of this flag.
    """
    run.check_stop()
    command = command.strip()
    if not command:
        return ExecResult(command, None, "", reason="empty command")

    decision = decide(command, run.auto_approve_reads)

    if decision.action == "DENY":
        return _blocked(run, command, actor, decision.reason)

    approver = actor
    if decision.action == "CONFIRM" and actor == "agent" and require_confirm:
        dec = await run.request_approval(
            "command", {"command": command, "reason": decision.reason}, purpose
        )
        if not dec.approved:
            run.audit.record(
                "command", command=command, actor=actor, approved=False,
                rejected=True, exit_code=None,
            )
            run.emit(
                EventType.COMMAND, command=command, actor=actor, rejected=True,
                exit_code=None, output_redacted="[rejected by technician]",
            )
            run.emit(
                EventType.TERM,
                data=f"$ {command}\r\n[REJECTED by technician]\r\n",
            )
            return ExecResult(command, None, "[rejected by technician]", rejected=True)
        if dec.edited:
            command = dec.edited.strip()
            # Re-classify the edited command; a human can never push it past DENY.
            if decide(command, run.auto_approve_reads).action == "DENY":
                return _blocked(run, command, actor, "edited command is unsafe")
        approver = "technician"

    if run.ssh is None or not run.ssh.connected:
        return ExecResult(command, None, "[no SSH connection]", reason="not connected")

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
    )
    if redacted_output:
        run.emit(EventType.TERM, data=redacted_output.replace("\n", "\r\n") + "\r\n")

    return ExecResult(
        command=command,
        exit_code=result.exit_code,
        output=redacted_output,
        timed_out=result.timed_out,
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
