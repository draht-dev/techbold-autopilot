"""Run registry and per-run coordination.

A Run owns its event stream (with history replay for late WebSocket joiners),
the SSH connection, the audit log, and the async primitives that let the agent
loop block on human input: per-command approvals, hypothesis selection, activity
submission, and a hard STOP that unblocks every pending wait.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

from app.audit import AuditLog
from app.config import Settings
from app.models import (
    EventType,
    Hypothesis,
    HypothesisComment,
    RunPhase,
    make_event,
    utcnow_iso,
)
from app.ssh import SSHRunner


class RunStopped(Exception):
    """Raised inside the agent loop when the technician hits STOP."""


@dataclass
class ApprovalDecision:
    approved: bool
    edited: Optional[str] = None


class Run:
    def __init__(
        self,
        run_id: str,
        ticket_id: int,
        settings: Settings,
        erp: Any,
        auto_approve_reads: bool,
    ) -> None:
        self.id = run_id
        self.ticket_id = ticket_id
        self.settings = settings
        self.erp = erp
        self.auto_approve_reads = auto_approve_reads

        self.phase = RunPhase.CONNECTING
        self.started_at = utcnow_iso()
        self.events: list[dict[str, Any]] = []
        self.subscribers: set[asyncio.Queue] = set()

        self.stop_event = asyncio.Event()
        self.pending_approvals: dict[str, asyncio.Future] = {}
        self.hypothesis_future: Optional[asyncio.Future] = None
        self.decision_future: Optional[asyncio.Future] = None
        self.activity_future: Optional[asyncio.Future] = None

        self.ssh: Optional[SSHRunner] = None
        self.shell: Any = None  # lazily-opened interactive PTY for the technician
        self._shell_lock = asyncio.Lock()
        self.audit = AuditLog(run_id, settings.audit_dir)

        self.ticket: Any = None
        self.system_info: Any = None
        self.hypotheses: list[Hypothesis] = []
        self.activity_draft: Optional[dict[str, Any]] = None
        self.task: Optional[asyncio.Task] = None
        self.error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Event stream
    # ------------------------------------------------------------------ #
    def emit(self, type_: EventType | str, **data: Any) -> dict[str, Any]:
        event = make_event(type_, **data)
        self.events.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def set_phase(self, phase: RunPhase) -> None:
        self.phase = phase
        self.emit(EventType.RUN_STATE, phase=phase.value)
        self.audit.record("phase", detail=phase.value)

    def info(self, text: str) -> None:
        self.emit(EventType.INFO, text=text)

    def stream_agent(
        self,
        *,
        kind: str = "assistant",
        text: str = "",
        reasoning: str = "",
        tool_calls: Optional[list[dict[str, Any]]] = None,
        **extra: Any,
    ) -> None:
        """Stream a step of the continuous agent's thinking into the dev window.

        This is purely for visibility (it is not part of the audited command
        trail), so a late WebSocket joiner can replay the agent's reasoning.
        """
        self.emit(
            EventType.AGENT_MESSAGE,
            kind=kind,
            text=text,
            reasoning=reasoning,
            tool_calls=tool_calls or [],
            **extra,
        )

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    # ------------------------------------------------------------------ #
    # Stop handling
    # ------------------------------------------------------------------ #
    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise RunStopped()

    def request_stop(self) -> None:
        self.stop_event.set()
        self.info("STOP requested by technician")

    async def _await_or_stop(self, fut: asyncio.Future) -> Any:
        stop_task = asyncio.ensure_future(self.stop_event.wait())
        try:
            await asyncio.wait({fut, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not stop_task.done():
                stop_task.cancel()
        if self.stop_event.is_set():
            if not fut.done():
                fut.cancel()
            raise RunStopped()
        return fut.result()

    @staticmethod
    def _new_future() -> asyncio.Future:
        return asyncio.get_event_loop().create_future()

    # ------------------------------------------------------------------ #
    # Human-in-the-loop gates
    # ------------------------------------------------------------------ #
    async def request_approval(
        self, kind: str, payload: dict[str, Any], purpose: str = ""
    ) -> ApprovalDecision:
        self.check_stop()
        approval_id = uuid4().hex[:8]
        fut = self._new_future()
        self.pending_approvals[approval_id] = fut
        self.emit(
            EventType.APPROVAL_REQUEST,
            id=approval_id,
            kind=kind,
            payload=payload,
            purpose=purpose,
        )
        try:
            decision: ApprovalDecision = await self._await_or_stop(fut)
        finally:
            self.pending_approvals.pop(approval_id, None)
        self.emit(
            EventType.APPROVAL_RESOLVED,
            id=approval_id,
            approved=decision.approved,
            edited=decision.edited,
        )
        return decision

    def resolve_approval(self, approval_id: str, approved: bool, edited: Optional[str]) -> bool:
        fut = self.pending_approvals.get(approval_id)
        if fut is not None and not fut.done():
            fut.set_result(ApprovalDecision(approved=approved, edited=edited))
            return True
        return False

    async def await_hypothesis_selection(self) -> dict[str, Any]:
        """Block until the technician selects a ranked hypothesis OR submits their own.

        Returns ``{"kind": "existing", "id": ...}`` or
        ``{"kind": "custom", "title", "reasoning", "checks"}``.
        """
        self.check_stop()
        fut = self._new_future()
        self.hypothesis_future = fut
        try:
            return await self._await_or_stop(fut)
        finally:
            self.hypothesis_future = None

    def select_hypothesis(self, hypothesis_id: str) -> bool:
        fut = self.hypothesis_future
        if fut is not None and not fut.done():
            fut.set_result({"kind": "existing", "id": hypothesis_id})
            return True
        return False

    def submit_custom_hypothesis(self, payload: dict[str, Any]) -> bool:
        """The technician proposes their OWN hypothesis instead of picking one."""
        fut = self.hypothesis_future
        if fut is not None and not fut.done():
            checks = payload.get("checks")
            if not checks and payload.get("proposed_check"):
                checks = [payload["proposed_check"]]
            fut.set_result(
                {
                    "kind": "custom",
                    "title": payload.get("title", ""),
                    "reasoning": payload.get("reasoning", ""),
                    "checks": checks or [],
                }
            )
            return True
        return False

    def comment_hypothesis(self, hypothesis_id: str, text: str, author: str = "technician") -> bool:
        """Attach a steering comment to an agent-suggested hypothesis."""
        text = (text or "").strip()
        if not text:
            return False
        hyp = next((h for h in self.hypotheses if h.id == hypothesis_id), None)
        if hyp is None:
            return False
        hyp.comments.append(HypothesisComment(author=author, text=text))
        self.audit.record("hypothesis_comment", hypothesis=hypothesis_id, text=text)
        self.emit(EventType.HYPOTHESES, items=[h.model_dump() for h in self.hypotheses])
        return True

    async def await_decision(
        self, question: str, options: list[str], context: str = ""
    ) -> str:
        """Block until the technician chooses one of ``options``.

        Used when the agent is blocked (e.g. it cannot reproduce the problem) and
        needs a human judgement call. Returns the chosen option string.
        """
        self.check_stop()
        decision_id = uuid4().hex[:8]
        fut = self._new_future()
        self.decision_future = fut
        self.emit(
            EventType.DECISION_REQUEST,
            id=decision_id,
            question=question,
            options=options,
            context=context,
        )
        self.audit.record("decision_request", text=question, options=options)
        try:
            choice = await self._await_or_stop(fut)
        finally:
            self.decision_future = None
        self.emit(EventType.DECISION_RESOLVED, id=decision_id, choice=choice)
        self.audit.record("decision", text=choice)
        return choice

    def resolve_decision(self, choice: str) -> bool:
        fut = self.decision_future
        if fut is not None and not fut.done():
            fut.set_result(choice)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Interactive terminal (PTY) for the technician — supports vim/htop/etc.
    # ------------------------------------------------------------------ #
    async def ensure_shell(self, cols: int = 120, rows: int = 30) -> Any:
        async with self._shell_lock:
            if self.shell is not None:
                return self.shell
            if self.ssh is None or not self.ssh.connected:
                return None
            self.shell = await self.ssh.open_shell(self._on_shell_data, cols=cols, rows=rows)
            self.audit.record("shell_open")
            return self.shell

    def _on_shell_data(self, data: str) -> None:
        # Mirror every byte of the interactive session into the UI terminal.
        self.emit(EventType.TERM, data=data)

    async def terminal_write(self, data: str, cols: Optional[int] = None, rows: Optional[int] = None) -> None:
        shell = await self.ensure_shell(cols or 120, rows or 30)
        if shell is None:
            self.emit(EventType.TERM, data="[no SSH connection]\r\n")
            return
        shell.write(data)

    async def terminal_resize(self, cols: int, rows: int) -> None:
        if self.shell is not None:
            self.shell.resize(cols, rows)

    async def close_shell(self) -> None:
        if self.shell is not None:
            try:
                self.shell.close()
            except Exception:  # noqa: BLE001
                pass
            self.shell = None

    async def await_activity_submission(self) -> Optional[dict[str, Any]]:
        self.check_stop()
        fut = self._new_future()
        self.activity_future = fut
        try:
            return await self._await_or_stop(fut)
        finally:
            self.activity_future = None

    def submit_activity(self, activity: Optional[dict[str, Any]]) -> bool:
        fut = self.activity_future
        if fut is not None and not fut.done():
            fut.set_result(activity)
            return True
        return False

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "phase": self.phase.value,
            "auto_approve_reads": self.auto_approve_reads,
            "error": self.error,
            "events": self.events,
        }


class RunManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.erp: Any = None
        self.runs: dict[str, Run] = {}

    def create_run(self, ticket_id: int, auto_approve_reads: Optional[bool]) -> Run:
        run_id = uuid4().hex[:12]
        if auto_approve_reads is None:
            auto_approve_reads = self.settings.auto_approve_reads_default
        run = Run(run_id, ticket_id, self.settings, self.erp, auto_approve_reads)
        self.runs[run_id] = run
        return run

    def get(self, run_id: str) -> Optional[Run]:
        return self.runs.get(run_id)

    async def shutdown(self) -> None:
        for run in self.runs.values():
            run.request_stop()
            if run.task and not run.task.done():
                run.task.cancel()
            await run.close_shell()
            if run.ssh:
                await run.ssh.close()
