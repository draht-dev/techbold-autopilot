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
from app.models import EventType, Hypothesis, RunPhase, make_event, utcnow_iso
from app.ssh import SSHRunner


class RunStopped(Exception):
    """Raised inside the agent loop when the technician hits STOP."""


@dataclass
class ApprovalDecision:
    approved: bool
    edited: Optional[str] = None


@dataclass
class HypothesisSelection:
    hypothesis_id: str
    comment: Optional[str] = None
    custom_title: Optional[str] = None


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
        self.activity_future: Optional[asyncio.Future] = None

        self.ssh: Optional[SSHRunner] = None
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

    async def await_hypothesis_selection(self) -> HypothesisSelection:
        self.check_stop()
        fut = self._new_future()
        self.hypothesis_future = fut
        try:
            return await self._await_or_stop(fut)
        finally:
            self.hypothesis_future = None

    def select_hypothesis(
        self, hypothesis_id: str, comment: Optional[str] = None
    ) -> bool:
        fut = self.hypothesis_future
        if fut is not None and not fut.done():
            fut.set_result(
                HypothesisSelection(
                    hypothesis_id=hypothesis_id,
                    comment=(comment or "").strip() or None,
                )
            )
            return True
        return False

    def submit_custom_hypothesis(
        self, title: str, comment: Optional[str] = None
    ) -> bool:
        text = (title or "").strip()
        if not text:
            return False
        fut = self.hypothesis_future
        if fut is not None and not fut.done():
            fut.set_result(
                HypothesisSelection(
                    hypothesis_id="custom",
                    custom_title=text,
                    comment=(comment or "").strip() or None,
                )
            )
            return True
        return False

    def annotate_hypothesis(self, hypothesis_id: str, comment: str) -> bool:
        for hyp in self.hypotheses:
            if hyp.id == hypothesis_id:
                hyp.comment = comment.strip() or None
                self.emit(
                    EventType.HYPOTHESES,
                    items=[h.model_dump() for h in self.hypotheses],
                )
                return True
        return False

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
            if run.ssh:
                await run.ssh.close()
