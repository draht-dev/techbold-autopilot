"""Orchestrator — SPEC §7.1.

Deterministic state machine that drives a troubleshooting session:

    TRIAGE → DIAGNOSE → PROPOSE_FIX → APPLY → VALIDATE → PERSIST_CHECK
    → DOCUMENT → (SUBMIT via submit())

The LLM only *proposes*; the orchestrator enforces all gates.
HARD_BLOCK commands are NEVER run, even if a human somehow approves them.
All command output is redacted before storage, display, or model feed-back.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.agent.contracts import (
    MalformedAgentResponse,
    build_user_message,
    history_to_contract_dicts,
)
from app.agent.doctrine import build_system_prompt
from app.agent.llm import BaseLLM, LLMError
from app.audit_log import AuditLog
from app.config import get_settings
from app.erp_client import ErpClient
from app.models import (
    ActivityCreate,
    Activity,
    AgentResponse,
    Customer,
    CustomerSystem,
    FinalReport,
    Phase,
    ProposedCommand,
    Ticket,
    TicketStatus,
)
from app.safety import Verdict, classify, redact
from app.ssh_runner import HardBlockedError, SSHRunnerError

# Optional activity_generator — built in parallel; guard gracefully.
try:
    from app.activity_generator import build_activity as _build_activity

    _ACTIVITY_GENERATOR_AVAILABLE = True
except ImportError:
    _ACTIVITY_GENERATOR_AVAILABLE = False

logger = logging.getLogger("app.agent.orchestrator")

# ---- output truncation constants ----
_HEAD_LINES = 60
_TAIL_LINES = 20


# ---------------------------------------------------------------------------
# Public protocol types
# ---------------------------------------------------------------------------


@dataclass
class ApprovalDecision:
    """Return value of SessionIO.request_approval."""

    decision: str  # "approved" | "edited" | "rejected"
    edited_command: str | None = None


class CommandRunner(Protocol):
    """Both SSHRunner and MockSSHRunner satisfy this interface."""

    async def connect(self) -> None: ...
    async def run(self, command: str, *, timeout: int | None = None): ...
    async def close(self) -> None: ...


class SessionIO(Protocol):
    """Implemented by Session (Wave 4); FakeIO in tests."""

    async def emit(self, event_type: str, data: dict) -> None: ...

    async def request_approval(
        self,
        *,
        action_id: str,
        command: str,
        classification: str,
        purpose: str,
        expected_effect: str,
        rollback: str,
    ) -> ApprovalDecision: ...

    async def request_batch_confirm(self, *, plan: list[dict]) -> bool: ...

    def aborted(self) -> bool: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_output(text: str) -> str:
    """Bound long output to head + tail to keep audit + prompts manageable."""
    lines = text.splitlines()
    if len(lines) <= _HEAD_LINES + _TAIL_LINES:
        return text
    omitted = len(lines) - _HEAD_LINES - _TAIL_LINES
    head = lines[:_HEAD_LINES]
    tail = lines[-_TAIL_LINES:]
    return "\n".join(head) + f"\n... [{omitted} lines omitted] ...\n" + "\n".join(tail)


def _redact_customer_system(cs: CustomerSystem) -> CustomerSystem:
    """Return a copy of CustomerSystem with notes redacted (SPEC §7.1)."""
    safe_system = cs.system.model_copy(update={"notes": redact(cs.system.notes)})
    return cs.model_copy(update={"system": safe_system})


def _fallback_activity(
    ticket_id: int,
    start_iso: str,
    audit_log: AuditLog,
    final: FinalReport | None,
) -> ActivityCreate:
    """Produce a minimal ActivityCreate when activity_generator is unavailable."""
    executed = audit_log.executed_entries()
    commands_list = [e.command for e in executed]
    commands_str = "; ".join(commands_list[:20]) if commands_list else "(none)"

    root_cause = final.root_cause if final else "See audit log."
    summary = final.summary if final else f"Troubleshooting session for ticket {ticket_id}."
    actions = final.actions_taken if final else ""
    validation = final.validation_result if final else ""

    return ActivityCreate(
        ticket_id=ticket_id,
        start_datetime=start_iso,
        end_datetime=_now_iso(),
        summary=redact(summary),
        root_cause=redact(root_cause),
        actions_taken=redact(actions),
        commands_summary=redact(commands_str),
        validation_result=redact(validation),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Drives the TRIAGE → … → DOCUMENT state machine for one session.

    Parameters
    ----------
    session_id:     Unique session identifier (used for logging/events).
    ticket_id:      ERP ticket this session is resolving.
    llm:            Configured LLM client (BaseLLM).
    runner:         SSH-compatible runner (SSHRunner or MockSSHRunner).
    audit_log:      Per-session AuditLog (append-only).
    io:             SessionIO for SSE emission and approval gating.
    erp_client:     Optional — needed only when ticket data is not pre-injected.
    settings:       Optional settings override; falls back to get_settings().
    ticket:         Pre-fetched Ticket (skips ERP fetch in TRIAGE if provided).
    customer_system: Pre-fetched CustomerSystem (skips ERP fetch if provided).
    customer:       Pre-fetched Customer (skips ERP fetch if provided).
    """

    def __init__(
        self,
        *,
        session_id: str,
        ticket_id: int,
        llm: BaseLLM,
        runner: CommandRunner,
        audit_log: AuditLog,
        io: SessionIO,
        erp_client: ErpClient | None = None,
        settings: Any = None,
        ticket: Ticket | None = None,
        customer_system: CustomerSystem | None = None,
        customer: Customer | None = None,
    ) -> None:
        self.session_id = session_id
        self.ticket_id = ticket_id
        self._llm = llm
        self._runner = runner
        self._audit_log = audit_log
        self._io = io
        self._erp_client = erp_client
        self._settings = settings or get_settings()

        # Pre-injected data (avoids ERP calls in TRIAGE)
        self._ticket: Ticket | None = ticket
        self._customer_system: CustomerSystem | None = customer_system
        self._customer: Customer | None = customer

        # Runtime state
        self._phase: str = "triage"
        self._hypotheses: list[dict[str, Any]] = []
        self._feedback_note: str | None = None
        self._final: FinalReport | None = None
        self._draft_activity: ActivityCreate | None = None
        self._last_failed_action: ProposedCommand | None = None

        # Validation / persist tracking
        self._validation_detail: list[str] = []
        self._persist_detail: list[str] = []
        self._in_validate: bool = False
        self._in_persist: bool = False
        self._persist_check_done: bool = False

        # Step and command counters
        self._steps: int = 0
        self._commands_executed: int = 0

        # Session timestamps
        self._start_iso: str = ""

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def draft_activity(self) -> ActivityCreate | None:
        return self._draft_activity

    @property
    def final_report(self) -> FinalReport | None:
        return self._final

    # ------------------------------------------------------------------
    # run() — main entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Execute the full TRIAGE → … → DOCUMENT state machine."""
        self._start_iso = _now_iso()

        # ---- TRIAGE ----
        await self._triage()
        if self._ticket is None:
            return  # error already emitted in _triage

        # ---- Main loop ----
        while self._steps < self._settings.agent_max_steps:
            if self._io.aborted():
                await self._io.emit(
                    "error",
                    {"where": "aborted", "message": "Session aborted by technician"},
                )
                return

            self._steps += 1

            # Build prompt context
            history = history_to_contract_dicts(self._audit_log.entries)
            cs_redacted = (
                _redact_customer_system(self._customer_system)
                if self._customer_system
                else None
            )
            user_message = build_user_message(
                ticket=self._ticket,
                customer_system=cs_redacted,
                customer=self._customer,
                phase=self._phase,
                history=history,
                hypotheses=self._hypotheses,
                extra_note=self._feedback_note,
            )
            self._feedback_note = None  # consume after use

            # Call LLM
            response = await self._call_llm(
                system_prompt=build_system_prompt(self._settings),
                user_message=user_message,
            )
            if response is None:
                # Error already emitted; graceful abort
                break

            # Emit thought
            if response.thought:
                await self._io.emit("thought", {"text": response.thought})

            # Update hypotheses
            if response.hypotheses:
                self._hypotheses = [h.model_dump() for h in response.hypotheses]
                await self._io.emit("hypotheses", {"items": self._hypotheses})

            # Check finish signal FIRST
            if response.ready_to_finish and response.final:
                self._final = response.final
                break  # proceed to DOCUMENT

            # Process proposed commands
            await self._process_response(response)

            # Check limits after processing
            if self._commands_executed >= self._settings.agent_max_commands:
                await self._io.emit(
                    "error",
                    {
                        "where": "limit",
                        "message": (
                            f"Reached agent_max_commands limit "
                            f"({self._settings.agent_max_commands}). "
                            "Proceeding to DOCUMENT with available data."
                        ),
                    },
                )
                break
        else:
            # Exceeded max steps
            await self._io.emit(
                "error",
                {
                    "where": "limit",
                    "message": (
                        f"Reached agent_max_steps limit "
                        f"({self._settings.agent_max_steps}). "
                        "Proceeding to DOCUMENT with available data."
                    ),
                },
            )

        await self._document()

    # ------------------------------------------------------------------
    # submit() — SUBMIT phase (called externally after human review)
    # ------------------------------------------------------------------

    async def submit(self, activity: ActivityCreate) -> Activity | None:
        """Post the reviewed activity to the ERP and mark the ticket DONE."""
        await self._io.emit("phase_change", {"phase": "submit"})

        if self._erp_client is None:
            await self._io.emit(
                "error",
                {"where": "erp_submit", "message": "No ERP client configured for submit."},
            )
            return None

        try:
            created = await self._erp_client.create_activity(activity)
        except Exception as exc:
            msg = redact(str(exc))
            logger.error("ERP create_activity failed: %s", msg)
            await self._io.emit("error", {"where": "erp_submit", "message": msg})
            raise

        try:
            await self._erp_client.set_status(self.ticket_id, TicketStatus.DONE)
        except Exception as exc:
            msg = redact(str(exc))
            logger.error("ERP set_status DONE failed: %s", msg)
            await self._io.emit("error", {"where": "erp_submit", "message": msg})
            raise

        await self._io.emit(
            "done", {"ticket_id": self.ticket_id, "status": "DONE"}
        )
        return created

    # ------------------------------------------------------------------
    # TRIAGE phase
    # ------------------------------------------------------------------

    async def _triage(self) -> None:
        self._phase = "triage"
        await self._io.emit("phase_change", {"phase": "triage"})

        # Fetch ticket data if not pre-injected
        if self._ticket is None or self._customer_system is None:
            if self._erp_client is None:
                await self._io.emit(
                    "error",
                    {
                        "where": "triage",
                        "message": "No ERP client and no pre-injected ticket data.",
                    },
                )
                return

        if self._ticket is None and self._erp_client is not None:
            try:
                self._ticket = await self._erp_client.get_ticket(self.ticket_id)
            except Exception as exc:
                await self._io.emit(
                    "error", {"where": "triage", "message": redact(str(exc))}
                )
                return

        if self._ticket is None:
            await self._io.emit(
                "error",
                {"where": "triage", "message": f"Ticket {self.ticket_id} not found."},
            )
            return

        if self._customer_system is None and self._erp_client is not None:
            try:
                self._customer_system = await self._erp_client.get_customer_system(
                    self.ticket_id
                )
            except Exception as exc:
                logger.warning("Could not fetch customer system: %s", redact(str(exc)))

        if (
            self._customer is None
            and self._erp_client is not None
            and self._ticket is not None
        ):
            try:
                self._customer = await self._erp_client.get_customer(
                    self._ticket.customer_id
                )
            except Exception as exc:
                logger.warning("Could not fetch customer: %s", redact(str(exc)))

        # Emit thought summarising the broken capability
        description_redacted = redact(self._ticket.description)
        await self._io.emit(
            "thought",
            {
                "text": (
                    f"Triage: ticket={self.ticket_id} "
                    f"title='{self._ticket.title}' "
                    f"description='{description_redacted[:200]}'"
                )
            },
        )

        # Connect to the remote system
        try:
            await self._runner.connect()
        except Exception as exc:
            msg = redact(str(exc))
            logger.error("SSH connect failed: %s", msg)
            await self._io.emit("error", {"where": "ssh_connect", "message": msg})
            self._ticket = None  # signal run() to return
            return

        # Advance to DIAGNOSE
        self._phase = "diagnose"
        await self._io.emit("phase_change", {"phase": "diagnose"})

    # ------------------------------------------------------------------
    # Process one LLM response
    # ------------------------------------------------------------------

    # Known valid phase strings from the Phase enum
    _KNOWN_PHASES: frozenset[str] = frozenset(p.value for p in Phase)

    async def _process_response(self, response: AgentResponse) -> None:
        """Classify commands, update phase, execute appropriately.

        FIX (Bug 1 + Bug 2): The model's declared ``response.phase`` is
        authoritative for the LABEL — we apply it BEFORE executing any
        commands so that audit entries and _validation_detail/_persist_detail
        are tagged with the correct phase.  The GATE (classify-based) is
        fully independent of the label.

        The mutating-command heuristic is kept only as a FALLBACK for when the
        model did not declare a recognised phase, and it NEVER overrides an
        explicit ``validate`` or ``persist_check`` declaration even if a
        mutating command is present.
        """
        commands = response.proposed_commands

        # ---- Step 1: resolve the phase LABEL before any command runs ----
        old_phase = self._phase
        resolved_phase = self._resolve_phase(response)
        if resolved_phase != old_phase:
            self._phase = resolved_phase
            await self._io.emit("phase_change", {"phase": self._phase})

        if not commands:
            return

        # ---- Step 2: classify commands ----
        read_only_cmds: list[ProposedCommand] = []
        gated_cmds: list[ProposedCommand] = []
        blocked_cmds: list[ProposedCommand] = []

        for cmd in commands:
            cls = classify(cmd.command)
            if cls.verdict == Verdict.HARD_BLOCK:
                blocked_cmds.append(cmd)
            elif cls.verdict == Verdict.READ_ONLY and not cmd.mutating:
                read_only_cmds.append(cmd)
            else:
                gated_cmds.append(cmd)

        # Handle blocked commands first — emit safety events and feedback
        for cmd in blocked_cmds:
            cls = classify(cmd.command)
            await self._io.emit(
                "safety_block",
                {"command": cmd.command, "reason": cls.reason},
            )
            self._audit_log.append(
                phase=self._phase,
                command=cmd.command,
                classification="hard_block",
                approval="hard_block",
                approved_by=None,
                exit_code=None,
                output_summary="",
                agent_rationale=cmd.purpose,
            )
            self._feedback_note = (
                f"The command `{cmd.command}` was BLOCKED by the safety layer "
                f"(reason: {cls.reason}). "
                "Do NOT attempt it or any equivalent. "
                "Find a safe, minimal alternative that fixes the root cause — "
                "e.g. fix directory ownership instead of running as root; "
                "open the specific firewall port instead of disabling the firewall; "
                "never delete data/logs."
            )

        # Handle read-only batch
        if read_only_cmds:
            plan = [
                {
                    "action_id": str(uuid.uuid4()),
                    "command": c.command,
                    "classification": "read_only",
                    "mutating": False,
                    "purpose": c.purpose,
                    "expected_effect": c.expected_effect,
                }
                for c in read_only_cmds
            ]
            await self._io.emit("plan", {"commands": plan})

            run_batch = True
            if not self._settings.auto_run_readonly:
                confirmed = await self._io.request_batch_confirm(plan=plan)
                if not confirmed:
                    self._feedback_note = (
                        "The technician declined to run the read-only batch. "
                        "Please propose a different set of diagnostic commands or explain "
                        "why the current approach is necessary."
                    )
                    run_batch = False

            if run_batch:
                for i, cmd in enumerate(read_only_cmds):
                    action_id = plan[i]["action_id"]
                    # Attach action_id to command for _execute
                    await self._execute(cmd, self._phase, action_id=action_id, auto_approved=True)
                    if self._commands_executed >= self._settings.agent_max_commands:
                        break

        # ---- Step 3: for gated (mutating) commands in propose_fix, advance
        #              the label to apply (the actual execution phase).
        #              For persist_check, mutating commands (e.g. systemctl restart)
        #              are gated but the phase label stays persist_check — the gate
        #              is classify-based, not phase-based.
        if gated_cmds:
            if self._phase == "propose_fix":
                # Transition to apply for execution; propose_fix was already emitted
                # above by _resolve_phase.
                self._phase = "apply"
                await self._io.emit("phase_change", {"phase": "apply"})
            elif self._phase not in ("apply", "validate", "persist_check"):
                # Unexpected phase with a mutating command — default to apply
                self._phase = "apply"
                await self._io.emit("phase_change", {"phase": "apply"})

        # Handle gated (mutating / needs_approval) commands
        for cmd in gated_cmds:
            if self._commands_executed >= self._settings.agent_max_commands:
                break
            await self._execute(cmd, self._phase)

        # ---- Step 4: Emit validation/persist data events ----
        # These fire once per turn in the relevant phase, using the detail
        # stashed in _execute().  The detail lists are populated by _execute
        # which receives self._phase at call time — now correctly set before
        # any command runs.
        if self._phase == "validate" and self._validation_detail:
            passed = all(
                e.exit_code == 0
                for e in self._audit_log.entries
                if e.phase == "validate" and e.exit_code is not None
            )
            detail_text = "\n".join(self._validation_detail[-10:])
            await self._io.emit("validation", {"passed": passed, "detail": detail_text})

        if self._phase == "persist_check" and self._persist_detail:
            passed = all(
                e.exit_code == 0
                for e in self._audit_log.entries
                if e.phase == "persist_check" and e.exit_code is not None
            )
            detail_text = "\n".join(self._persist_detail[-10:])
            await self._io.emit(
                "persist_check", {"passed": passed, "detail": detail_text}
            )

    def _resolve_phase(self, response: AgentResponse) -> str:
        """Return the phase label to use for this turn.

        The model's declared ``response.phase`` is authoritative when it names
        a recognised Phase enum value.  The mutating-command heuristic is
        applied only as a FALLBACK when the model declares an unrecognised or
        absent phase, and it NEVER overrides an explicit ``validate`` or
        ``persist_check`` declaration.

        SPEC §7.1: "the LLM advises, the orchestrator enforces" — the phase is
        a LABEL; the GATE (classify-based) is independent.
        """
        model_phase = (response.phase or "").strip().lower()

        if model_phase in self._KNOWN_PHASES:
            # Model explicitly declared a valid phase — honour it.
            # Exception: if the model declares 'document' or 'submit' we stay
            # in current phase (those are orchestrator-owned transitions).
            if model_phase in ("document", "submit", "triage"):
                return self._phase
            return model_phase

        # --- Fallback heuristic (model didn't declare a valid phase) ---
        # Classify to determine if there are mutating commands
        has_mutating = any(
            classify(cmd.command).verdict != Verdict.READ_ONLY
            and classify(cmd.command).verdict != Verdict.HARD_BLOCK
            for cmd in response.proposed_commands
        ) or any(
            cmd.mutating for cmd in response.proposed_commands
        )

        if has_mutating and self._phase not in ("propose_fix", "apply", "persist_check"):
            return "propose_fix"
        if response.ready_to_validate and self._phase == "diagnose":
            return "validate"
        return self._phase

    # ------------------------------------------------------------------
    # _execute — the ONLY place commands run
    # ------------------------------------------------------------------

    async def _execute(
        self,
        action: ProposedCommand,
        phase: str,
        *,
        action_id: str | None = None,
        auto_approved: bool = False,
    ) -> None:
        """Single choke-point for running a command (SPEC §7.1, §0.1, §4).

        Order:
        1. Classify the command.
        2. HARD_BLOCK → refuse, audit, set feedback, return.
        3. READ_ONLY → run directly (already confirmed by batch confirm or auto_run).
           NEEDS_APPROVAL → gate on human approval; handle reject/edit.
        4. Re-classify edited command.
        5. Run via runner; catch errors; redact output; audit; emit event.
        """
        if action_id is None:
            action_id = str(uuid.uuid4())

        cls = classify(action.command)

        # ---- 1: HARD_BLOCK ----
        if cls.verdict == Verdict.HARD_BLOCK:
            logger.warning(
                "HARD_BLOCK prevented: %s (rule=%s)", action.command, cls.matched_rule
            )
            await self._io.emit(
                "safety_block",
                {"command": action.command, "reason": cls.reason},
            )
            self._audit_log.append(
                phase=phase,
                command=action.command,
                classification="hard_block",
                approval="hard_block",
                approved_by=None,
                exit_code=None,
                output_summary="",
                agent_rationale=action.purpose,
            )
            self._feedback_note = (
                f"The command `{action.command}` was BLOCKED by the safety layer "
                f"(reason: {cls.reason}). "
                "Do NOT attempt it or any equivalent. "
                "Find a safe, minimal alternative that fixes the root cause — "
                "e.g. fix directory ownership instead of running as root; "
                "open the specific firewall port instead of disabling the firewall; "
                "never delete data/logs."
            )
            return

        # ---- 2: Determine command and approval label ----
        command_to_run = action.command
        approval_label: str
        approved_by: str | None = None

        if cls.verdict == Verdict.READ_ONLY and auto_approved:
            approval_label = "auto"
        else:
            # NEEDS_APPROVAL — gate on human
            # NOTE: propose_fix → apply transition is now handled in
            # _process_response before commands execute; no duplicate emit here.
            decision = await self._io.request_approval(
                action_id=action_id,
                command=action.command,
                classification=cls.verdict.value,
                purpose=action.purpose,
                expected_effect=action.expected_effect,
                rollback=action.rollback,
            )

            if decision.decision == "rejected":
                self._audit_log.append(
                    phase=phase,
                    command=action.command,
                    classification=cls.verdict.value,
                    approval="rejected",
                    approved_by=None,
                    exit_code=None,
                    output_summary="",
                    agent_rationale=action.purpose,
                )
                self._feedback_note = (
                    f"The technician REJECTED `{action.command}`. "
                    "Propose a safer/different approach or explain why it is unnecessary."
                )
                return

            if decision.decision == "edited" and decision.edited_command:
                command_to_run = decision.edited_command
                # Re-classify edited command — defense in depth
                edited_cls = classify(command_to_run)
                if edited_cls.verdict == Verdict.HARD_BLOCK:
                    logger.warning(
                        "Edited command is HARD_BLOCK: %s", command_to_run
                    )
                    await self._io.emit(
                        "safety_block",
                        {"command": command_to_run, "reason": edited_cls.reason},
                    )
                    self._audit_log.append(
                        phase=phase,
                        command=command_to_run,
                        classification="hard_block",
                        approval="hard_block",
                        approved_by="technician",
                        exit_code=None,
                        output_summary="",
                        agent_rationale=action.purpose,
                    )
                    self._feedback_note = (
                        f"The edited command `{command_to_run}` was BLOCKED by the safety layer "
                        f"(reason: {edited_cls.reason}). "
                        "Do NOT attempt this or equivalent commands."
                    )
                    return
                approval_label = "edited"
                approved_by = "technician"
            else:
                # approved
                approval_label = "approved"
                approved_by = "technician"

        # ---- 3: Run the command ----
        try:
            result = await self._runner.run(
                command_to_run, timeout=self._settings.ssh_command_timeout
            )
        except HardBlockedError as exc:
            # Defense in depth — the runner itself blocked it
            msg = redact(str(exc))
            await self._io.emit(
                "safety_block",
                {"command": command_to_run, "reason": msg},
            )
            self._audit_log.append(
                phase=phase,
                command=command_to_run,
                classification="hard_block",
                approval="hard_block",
                approved_by=None,
                exit_code=None,
                output_summary="",
                agent_rationale=action.purpose,
            )
            self._feedback_note = (
                f"The command `{command_to_run}` was BLOCKED at the runner level "
                f"(reason: {msg}). Do NOT attempt it or any equivalent."
            )
            return
        except (SSHRunnerError, Exception) as exc:
            msg = redact(str(exc))
            logger.error("SSH run error for command %r: %s", command_to_run, msg)
            await self._io.emit(
                "error",
                {"where": "ssh", "message": msg},
            )
            self._audit_log.append(
                phase=phase,
                command=command_to_run,
                classification=cls.verdict.value,
                approval=approval_label,
                approved_by=approved_by,
                exit_code=None,
                output_summary=msg,
                agent_rationale=action.purpose,
            )
            self._last_failed_action = action
            return

        # ---- 4: Redact output and build summary ----
        raw_out = (result.stdout or "") + (
            ("\n[stderr] " + result.stderr) if result.stderr else ""
        )
        red_out = redact(raw_out)
        output_summary = _truncate_output(red_out)

        # ---- 5: Audit ----
        self._audit_log.append(
            phase=phase,
            command=command_to_run,
            classification=cls.verdict.value,
            approval=approval_label,
            approved_by=approved_by,
            exit_code=result.exit_code,
            output_summary=output_summary,
            agent_rationale=action.purpose,
        )

        # ---- 6: Emit command_result ----
        await self._io.emit(
            "command_result",
            {
                "action_id": action_id,
                "command": command_to_run,
                "classification": cls.verdict.value,
                "approval": approval_label,
                "exit_code": result.exit_code,
                "output_summary": output_summary,
            },
        )

        self._commands_executed += 1

        # ---- 7: Stash validation / persist detail ----
        if phase == "validate":
            self._validation_detail.append(
                f"{command_to_run}: exit={result.exit_code} {output_summary[:200]}"
            )
        elif phase == "persist_check":
            self._persist_detail.append(
                f"{command_to_run}: exit={result.exit_code} {output_summary[:200]}"
            )

    # ------------------------------------------------------------------
    # LLM call with error handling
    # ------------------------------------------------------------------

    async def _call_llm(
        self, *, system_prompt: str, user_message: str
    ) -> AgentResponse | None:
        """Call the LLM with one retry on malformed response; return None on failure.

        The LLM coroutine is raced against an abort sentinel so that Abort
        during a slow LLM call bails promptly.  If aborted, the LLM task is
        cancelled and awaited cleanly to avoid 'task was never retrieved' warnings.
        """
        # Quick pre-call abort check
        if self._io.aborted():
            return None

        last_exc: Exception | None = None
        for attempt in range(2):
            if self._io.aborted():
                return None
            try:
                llm_task = asyncio.ensure_future(
                    self._llm.propose(
                        system_prompt=system_prompt,
                        user_message=user_message,
                    )
                )

                # Poll abort flag while waiting for the LLM
                # (simple interval-based race — avoids complex event wiring)
                response: AgentResponse | None = None
                while True:
                    done, pending = await asyncio.wait(
                        {llm_task}, timeout=0.5
                    )
                    if done:
                        response = await llm_task  # re-raise exceptions
                        break
                    if self._io.aborted():
                        llm_task.cancel()
                        try:
                            await llm_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        return None

                return response
            except (MalformedAgentResponse, LLMError) as exc:
                last_exc = exc
                logger.warning(
                    "LLM error (attempt %d/2): %s", attempt + 1, str(exc)
                )
                if attempt == 0:
                    # Give one retry
                    continue

        # Both attempts failed
        msg = redact(str(last_exc)) if last_exc else "Unknown LLM error"
        await self._io.emit("error", {"where": "llm", "message": msg})
        return None

    # ------------------------------------------------------------------
    # DOCUMENT phase
    # ------------------------------------------------------------------

    async def _document(self) -> None:
        """Build the activity draft, emit activity_draft, close the connection."""
        self._phase = "document"
        await self._io.emit("phase_change", {"phase": "document"})

        end_iso = _now_iso()

        if _ACTIVITY_GENERATOR_AVAILABLE:
            try:
                self._draft_activity = _build_activity(
                    ticket_id=self.ticket_id,
                    start_datetime=self._start_iso,
                    end_datetime=end_iso,
                    audit_log=self._audit_log,
                    final=self._final,
                )
            except Exception as exc:
                logger.warning(
                    "activity_generator.build_activity failed (%s); using fallback.",
                    redact(str(exc)),
                )
                self._draft_activity = _fallback_activity(
                    self.ticket_id, self._start_iso, self._audit_log, self._final
                )
        else:
            self._draft_activity = _fallback_activity(
                self.ticket_id, self._start_iso, self._audit_log, self._final
            )

        await self._io.emit(
            "activity_draft", {"activity": self._draft_activity.model_dump()}
        )

        try:
            await self._runner.close()
        except Exception as exc:
            logger.debug("Non-fatal error closing runner: %s", redact(str(exc)))
