"""Session and SessionManager — SPEC §7.1, §8, §9.

Session
-------
Implements the SessionIO protocol (used by the Orchestrator) and owns:
- The SSE event stream (``_events`` history + ``asyncio.Condition`` notifier).
- The human-approval gate (``request_approval`` / ``resolve_approval``).
- The agent task lifecycle (``start`` / ``retry`` / ``abort``).
- The ``submit_activity`` path (delegates to Orchestrator.submit).

SessionManager
--------------
Factory for Session objects.  Creates one Session per ticket-id by fetching
ERP data, building the runner/LLM/audit-log, wiring the Orchestrator, and
storing the session in an in-memory dict.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from app.agent.llm import BaseLLM, get_llm_client
from app.agent.orchestrator import ApprovalDecision, Orchestrator
from app.audit_log import AuditLog
from app.config import get_settings
from app.erp_client import ErpClient, make_erp_client
from app.models import (
    ActivityCreate,
    Activity,
    CustomerSystem,
    SSEEvent,
)
from app.ssh_runner import make_ssh_runner

logger = logging.getLogger("app.session")


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class SessionError(Exception):
    """Raised by SessionManager when a session cannot be created (e.g. ticket not found)."""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """Per-ticket troubleshooting session.

    Implements the SessionIO protocol expected by the Orchestrator::

        async emit(event_type, data) -> None
        async request_approval(...) -> ApprovalDecision
        async request_batch_confirm(*, plan) -> bool
        def aborted() -> bool

    Additionally exposes the API surface used by the routers:
        resolve_approval, abort, start, retry, submit_activity, subscribe
    """

    def __init__(
        self,
        *,
        session_id: str,
        ticket_id: int,
        runner,
        audit_log: AuditLog,
        erp_client: ErpClient,
        settings=None,
    ) -> None:
        self.session_id = session_id
        self.ticket_id = ticket_id
        self.runner = runner
        self.audit_log = audit_log
        self.erp_client = erp_client
        self.settings = settings or get_settings()

        # Orchestrator is attached after construction by SessionManager.
        self.orchestrator: Orchestrator | None = None

        # SSE event history + notification mechanism
        self._events: list[SSEEvent] = []
        self._condition: asyncio.Condition = asyncio.Condition()

        # Abort signal
        self._abort: asyncio.Event = asyncio.Event()

        # Pending approval futures keyed by action_id
        self._pending: dict[str, asyncio.Future] = {}

        # Agent task
        self._task: asyncio.Task | None = None

        # Session lifecycle status
        self._status: str = "created"

    # ------------------------------------------------------------------
    # SessionIO protocol
    # ------------------------------------------------------------------

    async def emit(self, event_type: str, data: dict) -> None:
        """Build an SSEEvent, append to history, and wake subscribers."""
        event = SSEEvent(
            type=event_type,
            session_id=self.session_id,
            ts=datetime.now(timezone.utc).isoformat(),
            data=data,
        )
        self._events.append(event)

        # Update status based on key events
        if event_type == "done":
            self._status = "done"
        elif event_type == "activity_draft":
            self._status = "awaiting_review"
        elif event_type == "error":
            if self._status not in ("done", "aborted"):
                self._status = "error"

        async with self._condition:
            self._condition.notify_all()

    async def request_approval(
        self,
        *,
        action_id: str,
        command: str,
        classification: str,
        purpose: str,
        expected_effect: str,
        rollback: str,
    ) -> ApprovalDecision:
        """Gate a mutating command on human approval.

        Creates a Future, emits ``awaiting_approval``, then awaits the future
        raced against the abort event.  Returns immediately with
        ``ApprovalDecision("rejected")`` if the session is aborted.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[action_id] = fut
        self._status = "awaiting_review"

        await self.emit(
            "awaiting_approval",
            {
                "action_id": action_id,
                "command": command,
                "classification": classification,
                "purpose": purpose,
                "expected_effect": expected_effect,
                "rollback": rollback,
            },
        )

        # Race: approval future vs abort
        abort_task = asyncio.ensure_future(self._abort.wait())
        try:
            done, _ = await asyncio.wait(
                {fut, abort_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not abort_task.done():
                abort_task.cancel()
                try:
                    await abort_task
                except asyncio.CancelledError:
                    pass

        self._pending.pop(action_id, None)

        if self._abort.is_set() and fut not in done:
            return ApprovalDecision("rejected")

        if fut in done and not fut.cancelled():
            try:
                return fut.result()
            except Exception:
                return ApprovalDecision("rejected")

        return ApprovalDecision("rejected")

    async def request_batch_confirm(self, *, plan: list[dict]) -> bool:
        """Ask the technician to confirm a read-only command batch.

        If ``auto_run_readonly`` is True in settings, confirms automatically.
        Otherwise creates a Future keyed ``"batch-confirm"`` and waits for the
        technician to POST approve with ``action_id="batch-confirm"``.
        """
        if self.settings.auto_run_readonly:
            return True

        loop = asyncio.get_event_loop()
        action_id = "batch-confirm"
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[action_id] = fut

        abort_task = asyncio.ensure_future(self._abort.wait())
        try:
            done, _ = await asyncio.wait(
                {fut, abort_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not abort_task.done():
                abort_task.cancel()
                try:
                    await abort_task
                except asyncio.CancelledError:
                    pass

        self._pending.pop(action_id, None)

        if self._abort.is_set() and fut not in done:
            return False

        if fut in done and not fut.cancelled():
            try:
                decision = fut.result()
                return decision.decision != "rejected"
            except Exception:
                return False

        return False

    def aborted(self) -> bool:
        """Return True if this session has been aborted."""
        return self._abort.is_set()

    # ------------------------------------------------------------------
    # Resolution / control API (called from routers)
    # ------------------------------------------------------------------

    def resolve_approval(self, action_id: str, decision: ApprovalDecision) -> None:
        """Resolve a pending approval future.

        Tolerates unknown or already-resolved action_ids (does not raise).
        """
        fut = self._pending.get(action_id)
        if fut is None:
            return
        if fut.done():
            return
        try:
            fut.set_result(decision)
        except asyncio.InvalidStateError:
            pass

    def abort(self) -> None:
        """Signal abort: set the abort event and reject all pending approvals."""
        self._abort.set()
        self._status = "aborted"

        # Unblock any waiting futures
        for action_id, fut in list(self._pending.items()):
            if not fut.done():
                try:
                    fut.set_result(ApprovalDecision("rejected"))
                except asyncio.InvalidStateError:
                    pass
        self._pending.clear()

        # Wake SSE subscribers so they can see the abort
        asyncio.ensure_future(self._notify_abort())

    async def _notify_abort(self) -> None:
        """Emit the abort error event and wake SSE subscribers."""
        await self.emit(
            "error",
            {"where": "aborted", "message": "Session aborted by technician"},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the orchestrator agent loop as a background asyncio task."""
        if self._task is not None and not self._task.done():
            logger.debug("Session %s: start() called but task already running", self.session_id)
            return
        self._status = "running"
        self._task = asyncio.create_task(self._run_guarded())

    async def _run_guarded(self) -> None:
        """Run the orchestrator, catching all errors so the task never dies silently."""
        if self.orchestrator is None:
            await self.emit(
                "error",
                {"where": "session", "message": "Orchestrator not configured."},
            )
            return
        try:
            await self.orchestrator.run()
        except Exception as exc:
            msg = str(exc)
            logger.exception("Session %s: unhandled error in agent loop: %s", self.session_id, msg)
            await self.emit(
                "error",
                {"where": "agent_loop", "message": msg},
            )
        finally:
            try:
                await self.runner.close()
            except Exception as exc:
                logger.debug("Session %s: non-fatal runner close error: %s", self.session_id, exc)

    async def retry(self) -> None:
        """Best-effort retry: restart the agent loop if not already running.

        If the previous task is still alive, emits a note and returns.
        Reuses the same runner, audit_log and orchestrator (they carry state).
        """
        if self._task is not None and not self._task.done():
            await self.emit(
                "thought",
                {"text": "retry ignored: session still running"},
            )
            return

        if self._status == "done":
            await self.emit(
                "thought",
                {"text": "retry ignored: session already done"},
            )
            return

        # Re-run the loop
        await self.emit("phase_change", {"phase": "retry"})
        self._status = "running"
        self._task = asyncio.create_task(self._run_guarded())

    async def submit_activity(self, activity: ActivityCreate) -> Activity | None:
        """Delegate activity submission to the orchestrator (SUBMIT phase)."""
        if self.orchestrator is None:
            raise RuntimeError("Orchestrator not configured")
        return await self.orchestrator.submit(activity)

    # ------------------------------------------------------------------
    # SSE subscription
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[SSEEvent]:
        """Async generator yielding SSEEvents.

        First replays all events already in history, then waits for new events
        as they arrive.  Stops after a terminal event (done / final error /
        aborted) has been yielded.
        """
        _TERMINAL_TYPES = {"done", "aborted"}
        _ERROR_TERMINAL = "error"

        pos = 0
        while True:
            async with self._condition:
                # Drain any accumulated events
                while pos < len(self._events):
                    event = self._events[pos]
                    pos += 1
                    yield event

                    # Check for terminal condition
                    if event.type in _TERMINAL_TYPES:
                        return
                    if event.type == _ERROR_TERMINAL and self._status in (
                        "error",
                        "aborted",
                        "done",
                    ):
                        # Only stop on error if the session is truly terminal
                        if self._task is not None and self._task.done():
                            return

                # Wait for the next notification
                if self._status in ("done", "aborted"):
                    # Session is terminal; no more events will arrive
                    return
                try:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    # Heartbeat / keep-alive: yield nothing; loop back to check
                    pass


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """Factory and registry for Session objects.

    Parameters
    ----------
    erp_client:
        Optional injected ErpClient (default: ``make_erp_client()``).
    runner_factory:
        Optional injected callable ``(CustomerSystem) -> runner``.
        Default builds an SSHRunner from the customer system's SSH target.
    llm_factory:
        Optional injected callable ``() -> BaseLLM``.
        Default: ``get_llm_client()``.
    settings:
        Optional Settings override; falls back to ``get_settings()``.
    """

    def __init__(
        self,
        *,
        erp_client: ErpClient | None = None,
        runner_factory=None,
        llm_factory=None,
        settings=None,
    ) -> None:
        self.settings = settings or get_settings()

        self.erp_client: ErpClient = erp_client or make_erp_client(self.settings)

        if runner_factory is None:
            def _default_runner_factory(customer_system: CustomerSystem):
                sys = customer_system.system
                return make_ssh_runner(
                    host=sys.ip,
                    port=sys.port or None,
                    username=sys.username or None,
                    settings=self.settings,
                )
            self._runner_factory = _default_runner_factory
        else:
            self._runner_factory = runner_factory

        if llm_factory is None:
            self._llm_factory = lambda: get_llm_client(self.settings)
        else:
            self._llm_factory = llm_factory

        self._sessions: dict[str, Session] = {}

    async def create(self, ticket_id: int) -> Session:
        """Create and register a new Session for the given ticket.

        Fetches ticket, customer-system, and customer from ERP.
        Raises SessionError (HTTP 404) if the ticket does not exist.
        """
        session_id = uuid.uuid4().hex

        # Fetch ticket
        ticket = await self.erp_client.get_ticket(ticket_id)
        if ticket is None:
            raise SessionError(f"Ticket {ticket_id} not found")

        # Fetch customer system (may be None for tickets without a linked system)
        customer_system = None
        try:
            customer_system = await self.erp_client.get_customer_system(ticket_id)
        except Exception as exc:
            logger.warning("Could not fetch customer system for ticket %d: %s", ticket_id, exc)

        # Fetch customer if we have a system
        customer = None
        if customer_system is not None:
            try:
                customer = await self.erp_client.get_customer(customer_system.customer_id)
            except Exception as exc:
                logger.warning("Could not fetch customer %d: %s", customer_system.customer_id, exc)

        # Build runner
        runner = None
        if customer_system is not None:
            try:
                runner = self._runner_factory(customer_system)
            except Exception as exc:
                logger.warning("Could not build runner for ticket %d: %s", ticket_id, exc)

        if runner is None:
            # Create a no-op runner stub so orchestrator still runs (will error on connect)
            runner = _NullRunner()

        # Build dependencies
        llm: BaseLLM = self._llm_factory()
        audit_log = AuditLog(session_id)

        # Build session
        session = Session(
            session_id=session_id,
            ticket_id=ticket_id,
            runner=runner,
            audit_log=audit_log,
            erp_client=self.erp_client,
            settings=self.settings,
        )

        # Build orchestrator and attach to session
        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=ticket_id,
            llm=llm,
            runner=runner,
            audit_log=audit_log,
            io=session,
            erp_client=self.erp_client,
            settings=self.settings,
            ticket=ticket,
            customer_system=customer_system,
            customer=customer,
        )
        session.orchestrator = orchestrator

        self._sessions[session_id] = session
        logger.info("Session %s created for ticket %d", session_id, ticket_id)
        return session

    def get(self, session_id: str) -> Session | None:
        """Return the Session for the given session_id, or None."""
        return self._sessions.get(session_id)


# ---------------------------------------------------------------------------
# Null runner — placeholder when no customer system is available
# ---------------------------------------------------------------------------


class _NullRunner:
    """A no-op runner that raises an error on connect (no real SSH target)."""

    async def connect(self) -> None:
        raise RuntimeError(
            "No customer system available for this ticket — cannot open SSH connection."
        )

    async def run(self, command: str, *, timeout: int | None = None):
        raise RuntimeError("No SSH connection established.")

    async def close(self) -> None:
        pass
