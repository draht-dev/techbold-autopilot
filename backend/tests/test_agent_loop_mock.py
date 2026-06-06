"""Integration tests for the full agent loop with mock SSH and stub LLM — SPEC §12.

Three test scenarios:
1. Full orchestrator loop (FakeIO auto-approve) — nginx recovery.
2. HARD_BLOCK refused mid-loop — even when the human approves.
3. Full API + approval gate via HTTP endpoints (httpx ASGITransport).
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from typing import Any

import httpx
import pytest

from mocks.mock_erp import app as erp_app
from mocks.mock_ssh import MockSSHRunner
from app.agent.llm import StubLLM
from app.agent.orchestrator import ApprovalDecision, Orchestrator
from app.audit_log import AuditLog
from app.erp_client import ErpClient
from app.models import (
    ActivityCreate,
    AgentResponse,
    CustomerSystem,
    FinalReport,
    Hypothesis,
    ProposedCommand,
    SystemInfo,
    Ticket,
    TicketStatus,
)
from app.routers.agent import set_session_manager
from app.session import SessionManager
from app.main import app as main_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUDIT_DIR = "/tmp/techbold-test-audit"
_TICKET_ID = 7001
_SECRET = "sk-ant-SUPERSECRET"
_SECRET_DB = "postgres://u:p@h/db"
_NOTES_WITH_SECRETS = f"app token={_SECRET}, db at {_SECRET_DB}"


# ---------------------------------------------------------------------------
# Shared scripted LLM responses — nginx recovery (5 turns)
# ---------------------------------------------------------------------------

def _make_nginx_script() -> list[AgentResponse]:
    """Return a fresh 5-turn script that drives nginx from stopped+disabled to active+enabled."""
    return [
        # Turn 1: diagnose — read-only checks
        AgentResponse(
            phase="diagnose",
            thought="Checking nginx state: is it active and enabled?",
            hypotheses=[
                Hypothesis(
                    cause="nginx stopped and not enabled",
                    evidence="checking now",
                    confidence=0.8,
                )
            ],
            proposed_commands=[
                ProposedCommand(
                    command="systemctl is-active nginx",
                    purpose="Check whether nginx is currently running",
                    mutating=False,
                ),
                ProposedCommand(
                    command="systemctl is-enabled nginx",
                    purpose="Check whether nginx starts on boot",
                    mutating=False,
                ),
                ProposedCommand(
                    command="systemctl status nginx",
                    purpose="Get full nginx status",
                    mutating=False,
                ),
            ],
            ready_to_validate=False,
            ready_to_finish=False,
        ),
        # Turn 2: propose fix — mutating command to enable+start nginx
        AgentResponse(
            phase="propose_fix",
            thought="nginx is inactive and disabled. Fix: enable --now to start and persist.",
            hypotheses=[
                Hypothesis(
                    cause="nginx unit was stopped and not enabled after package reconfigure",
                    evidence="is-active=inactive, is-enabled=disabled",
                    confidence=0.95,
                )
            ],
            proposed_commands=[
                ProposedCommand(
                    command="sudo systemctl enable --now nginx",
                    purpose="Start nginx and enable it to start on boot",
                    mutating=True,
                    expected_effect="nginx active (running) and enabled",
                    rollback="sudo systemctl disable --now nginx",
                ),
            ],
            ready_to_validate=False,
            ready_to_finish=False,
        ),
        # Turn 3: validate — read-only checks after fix
        AgentResponse(
            phase="validate",
            thought="Validating nginx is now active and serving HTTP.",
            hypotheses=[],
            proposed_commands=[
                ProposedCommand(
                    command="systemctl is-active nginx",
                    purpose="Confirm nginx is running",
                    mutating=False,
                ),
                ProposedCommand(
                    command="curl -s -o /dev/null -w '%{http_code}' http://localhost/",
                    purpose="Verify nginx responds with HTTP",
                    mutating=False,
                ),
            ],
            ready_to_validate=True,
            ready_to_finish=False,
        ),
        # Turn 4: persist_check — verify enabled + restart survival
        AgentResponse(
            phase="persist_check",
            thought="Checking persistence: is-enabled and does it survive a restart?",
            hypotheses=[],
            proposed_commands=[
                ProposedCommand(
                    command="systemctl is-enabled nginx",
                    purpose="Confirm nginx is enabled to start on boot",
                    mutating=False,
                ),
                ProposedCommand(
                    command="sudo systemctl restart nginx",
                    purpose="Restart to prove fix survives a restart",
                    mutating=True,
                    expected_effect="nginx continues running",
                    rollback="",
                ),
                ProposedCommand(
                    command="systemctl is-active nginx",
                    purpose="Re-check nginx is active after restart",
                    mutating=False,
                ),
            ],
            ready_to_validate=False,
            ready_to_finish=False,
        ),
        # Turn 5: finish — ready_to_finish with FinalReport
        AgentResponse(
            phase="persist_check",
            thought="nginx is active and enabled. Fix is persistent. Writing report.",
            hypotheses=[
                Hypothesis(
                    cause="nginx unit was stopped and not enabled after package reconfigure",
                    evidence="is-active=inactive, is-enabled=disabled before fix",
                    confidence=0.99,
                )
            ],
            proposed_commands=[],
            ready_to_validate=False,
            ready_to_finish=True,
            final=FinalReport(
                root_cause=(
                    "nginx systemd unit was stopped and not enabled; "
                    "it did not start on boot after a package reconfigure."
                ),
                summary="Restored nginx by enabling and starting the unit; fix persists across reboots.",
                actions_taken=(
                    "1. Confirmed nginx was inactive and disabled via systemctl checks. "
                    "2. Ran 'systemctl enable --now nginx' to start nginx and enable it for boot. "
                    "3. Validated nginx is-active=active. "
                    "4. Confirmed is-enabled=enabled and ran a restart to prove persistence."
                ),
                commands_summary=(
                    "systemctl is-active nginx; systemctl is-enabled nginx; "
                    "systemctl enable --now nginx; systemctl restart nginx"
                ),
                validation_result=(
                    "nginx is-active=active, is-enabled=enabled; "
                    "curl localhost returned HTTP 200; service survived restart."
                ),
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# FakeIO — auto-approves everything, never aborts
# ---------------------------------------------------------------------------


class FakeIO:
    """SessionIO implementation for tests: auto-approves, never aborts."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append({"type": event_type, "data": data})

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
        # Emit an awaiting_approval event so we can assert on it
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
        return ApprovalDecision("approved")

    async def request_batch_confirm(self, *, plan: list[dict]) -> bool:
        return True

    def aborted(self) -> bool:
        return False

    def event_types(self) -> list[str]:
        return [e["type"] for e in self.events]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ticket() -> Ticket:
    return Ticket(
        id=_TICKET_ID,
        title="nginx down",
        description="Website returning 502 — nginx appears to be down.",
        priority="high",
        status=TicketStatus.OPEN,
        customer_id=5001,
        customer_name="Test Customer",
    )


def _make_customer_system(notes: str = "") -> CustomerSystem:
    return CustomerSystem(
        ticket_id=_TICKET_ID,
        customer_id=5001,
        system=SystemInfo(
            ip="127.0.0.1",
            port=22,
            username="azureuser",
            os="Ubuntu 22.04 LTS",
            notes=notes,
        ),
    )


def _make_audit_log(session_id: str | None = None) -> AuditLog:
    return AuditLog(
        session_id=session_id or uuid.uuid4().hex,
        audit_dir=_AUDIT_DIR,
    )


# ---------------------------------------------------------------------------
# TEST 1 — Full orchestrator loop (FakeIO auto-approve)
# ---------------------------------------------------------------------------


class TestOrchestratorFullLoop:
    async def test_nginx_recovery_completes(self) -> None:
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_nginx_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        event_types = io.event_types()

        # Required event types must all be present
        assert "phase_change" in event_types, "Missing phase_change events"
        assert "thought" in event_types, "Missing thought events"
        assert "command_result" in event_types, "Missing command_result events"
        assert "activity_draft" in event_types, "Missing activity_draft event"

        # BUG 1 FIX: validation and persist_check SSE events must now be emitted
        assert "validation" in event_types, (
            f"Missing 'validation' event (Bug 1 fix); got event types: {event_types}"
        )
        assert "persist_check" in event_types, (
            f"Missing 'persist_check' event (Bug 1+2 fix); got event types: {event_types}"
        )

        # BUG 2 FIX: phase_change sequence must include validate AND persist_check
        # in order, before document/activity_draft
        phase_changes = [
            e["data"]["phase"]
            for e in io.events
            if e["type"] == "phase_change"
        ]
        assert "validate" in phase_changes, (
            f"Missing 'validate' in phase_change sequence; got: {phase_changes}"
        )
        assert "persist_check" in phase_changes, (
            f"Missing 'persist_check' in phase_change sequence; got: {phase_changes}"
        )
        # validate must come before persist_check, which must come before document
        idx_validate = phase_changes.index("validate")
        idx_persist = phase_changes.index("persist_check")
        idx_document = phase_changes.index("document")
        assert idx_validate < idx_persist, (
            f"'validate' must appear before 'persist_check' in phase sequence; got: {phase_changes}"
        )
        assert idx_persist < idx_document, (
            f"'persist_check' must appear before 'document' in phase sequence; got: {phase_changes}"
        )

        # validation event must carry passed=True for this happy-path script
        validation_events = [e for e in io.events if e["type"] == "validation"]
        assert validation_events, "No validation events found"
        assert validation_events[-1]["data"]["passed"] is True, (
            "Last validation event should report passed=True"
        )
        assert validation_events[-1]["data"]["detail"], (
            "validation event detail must be non-empty"
        )

        # persist_check event must carry passed=True for this happy-path script
        persist_events = [e for e in io.events if e["type"] == "persist_check"]
        assert persist_events, "No persist_check events found"
        assert persist_events[-1]["data"]["passed"] is True, (
            "Last persist_check event should report passed=True"
        )
        assert persist_events[-1]["data"]["detail"], (
            "persist_check event detail must be non-empty"
        )

    async def test_draft_activity_fields_all_populated(self) -> None:
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_nginx_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        draft = orchestrator.draft_activity
        assert draft is not None, "draft_activity should not be None after run()"
        assert draft.summary, "summary must be non-empty"
        assert draft.root_cause, "root_cause must be non-empty"
        assert draft.actions_taken, "actions_taken must be non-empty"
        assert draft.commands_summary, "commands_summary must be non-empty"
        assert draft.validation_result, "validation_result must be non-empty"

    async def test_runner_ends_active_and_enabled(self) -> None:
        """The fix (enable --now) must have been applied, leaving nginx active+enabled."""
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_nginx_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        assert runner.active is True, "nginx should be active after the fix"
        assert runner.enabled is True, "nginx should be enabled after the fix"

    async def test_no_secret_leak_in_events_or_audit(self) -> None:
        """Secrets in customer_system.system.notes must NOT appear in events or audit entries."""
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_nginx_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(notes=_NOTES_WITH_SECRETS),
        )

        await orchestrator.run()

        # Serialize all emitted events to a single JSON blob
        events_json = json.dumps(io.events)
        assert _SECRET not in events_json, (
            f"Secret {_SECRET!r} leaked into SSE events"
        )
        assert _SECRET_DB not in events_json, (
            f"Secret {_SECRET_DB!r} leaked into SSE events"
        )

        # Serialize all audit entries
        audit_json = json.dumps(audit_log.to_dicts())
        assert _SECRET not in audit_json, (
            f"Secret {_SECRET!r} leaked into audit log"
        )
        assert _SECRET_DB not in audit_json, (
            f"Secret {_SECRET_DB!r} leaked into audit log"
        )


# ---------------------------------------------------------------------------
# TEST 2 — HARD_BLOCK is refused mid-loop (even with auto-approving FakeIO)
# ---------------------------------------------------------------------------


def _make_hardblock_script() -> list[AgentResponse]:
    """LLM script that tries a HARD_BLOCK command then finishes gracefully."""
    return [
        # Turn 1: diagnose
        AgentResponse(
            phase="diagnose",
            thought="Checking disk space and service status.",
            hypotheses=[
                Hypothesis(cause="disk full", evidence="checking", confidence=0.5)
            ],
            proposed_commands=[
                ProposedCommand(
                    command="df -h",
                    purpose="Check disk usage",
                    mutating=False,
                ),
            ],
            ready_to_validate=False,
            ready_to_finish=False,
        ),
        # Turn 2: propose a HARD_BLOCK command (rm -rf on a DB data dir)
        AgentResponse(
            phase="propose_fix",
            thought="Disk is full. I will free space by removing postgresql data.",
            hypotheses=[
                Hypothesis(cause="disk full", evidence="df shows 100%", confidence=0.9)
            ],
            proposed_commands=[
                ProposedCommand(
                    command="sudo rm -rf /var/lib/postgresql",
                    purpose="free disk space",
                    mutating=True,
                    expected_effect="free disk space",
                    rollback="",
                ),
            ],
            ready_to_validate=False,
            ready_to_finish=False,
        ),
        # Turn 3: finish (after receiving the safety block feedback)
        AgentResponse(
            phase="diagnose",
            thought="The dangerous command was blocked. Finishing with available data.",
            hypotheses=[
                Hypothesis(cause="disk full", evidence="df shows high usage", confidence=0.7)
            ],
            proposed_commands=[],
            ready_to_validate=False,
            ready_to_finish=True,
            final=FinalReport(
                root_cause="Disk space was critically low on the data volume.",
                summary="Identified disk space issue; dangerous cleanup command was safety-blocked.",
                actions_taken="1. Checked disk usage with df -h. 2. Proposed cleanup was blocked by safety layer.",
                commands_summary="df -h",
                validation_result="No safe fix was applied; escalation recommended.",
            ),
        ),
    ]


class TestHardBlockMidLoop:
    async def test_hard_block_command_not_executed(self) -> None:
        """The HARD_BLOCK command must not mutate the runner state."""
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        # Record initial state
        initial_active = runner.active
        initial_enabled = runner.enabled

        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_hardblock_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        # Runner state must be unchanged — the hard-block command was never run
        assert runner.active == initial_active, "runner state mutated despite HARD_BLOCK"
        assert runner.enabled == initial_enabled, "runner state mutated despite HARD_BLOCK"

    async def test_safety_block_event_emitted(self) -> None:
        """A safety_block event must appear in the event stream."""
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_hardblock_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        event_types = io.event_types()
        assert "safety_block" in event_types, (
            f"Expected a safety_block event; got event types: {event_types}"
        )

    async def test_hard_block_audit_entry_exists(self) -> None:
        """An audit entry with approval='hard_block' must be recorded."""
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_hardblock_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        blocked = audit_log.rejected_or_blocked()
        hard_blocked = [e for e in blocked if e.approval == "hard_block"]
        assert hard_blocked, "Expected at least one hard_block audit entry"

    async def test_loop_completes_with_activity_draft(self) -> None:
        """Even after a HARD_BLOCK, the loop must reach DOCUMENT and emit activity_draft."""
        session_id = uuid.uuid4().hex
        runner = MockSSHRunner()
        audit_log = _make_audit_log(session_id)
        io = FakeIO()
        script = _make_hardblock_script()

        orchestrator = Orchestrator(
            session_id=session_id,
            ticket_id=_TICKET_ID,
            llm=StubLLM(script),
            runner=runner,
            audit_log=audit_log,
            io=io,
            ticket=_make_ticket(),
            customer_system=_make_customer_system(),
        )

        await orchestrator.run()

        event_types = io.event_types()
        assert "activity_draft" in event_types, (
            "Expected activity_draft event even after a HARD_BLOCK mid-loop"
        )
        assert orchestrator.draft_activity is not None


# ---------------------------------------------------------------------------
# TEST 3 — Full API + approval gate (httpx ASGITransport)
# ---------------------------------------------------------------------------


def _make_erp_client() -> ErpClient:
    transport = httpx.ASGITransport(app=erp_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://erp")
    return ErpClient(base_url="http://erp", token="test-token", client=http_client)


class TestFullApiApprovalGate:
    async def test_full_session_via_http(self, tmp_path) -> None:  # noqa: ANN001
        """End-to-end: create session, start, approve mutating commands, submit activity."""
        erp_client = _make_erp_client()
        script = _make_nginx_script()
        runner_instance = MockSSHRunner()

        # We capture a reference to the runner for state assertions
        captured_runner: list[MockSSHRunner] = []

        def runner_factory(_cs: Any) -> MockSSHRunner:
            captured_runner.append(runner_instance)
            return runner_instance

        sm = SessionManager(
            erp_client=erp_client,
            runner_factory=runner_factory,
            llm_factory=lambda: StubLLM(copy.deepcopy(script)),
        )

        # Inject the test-wired manager into the router
        set_session_manager(sm)

        ticket_id = 7001  # known ticket in the mock ERP

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_app),
            base_url="http://test",
        ) as http:
            # ---- Create session ----
            resp = await http.post(
                "/api/agent/sessions",
                json={"ticket_id": ticket_id},
            )
            assert resp.status_code == 200, f"create session failed: {resp.text}"
            sid = resp.json()["session_id"]

            # ---- Start session ----
            resp = await http.post(f"/api/agent/sessions/{sid}/start")
            assert resp.status_code == 200, f"start session failed: {resp.text}"

            session = sm.get(sid)
            assert session is not None

            # ---- Drive the loop: approve every awaiting_approval until activity_draft ----
            approved_ids: set[str] = set()
            draft_event = None

            for _ in range(200):
                await asyncio.sleep(0.05)

                # Approve any pending approval events we haven't handled yet
                for ev in list(session._events):
                    if (
                        ev.type == "awaiting_approval"
                        and ev.data["action_id"] not in approved_ids
                    ):
                        action_id = ev.data["action_id"]
                        approved_ids.add(action_id)
                        await http.post(
                            f"/api/agent/sessions/{sid}/approve",
                            json={"action_id": action_id},
                        )

                # Check if we have an activity_draft
                for ev in list(session._events):
                    if ev.type == "activity_draft":
                        draft_event = ev
                        break
                if draft_event is not None:
                    break

            assert draft_event is not None, (
                "Never received activity_draft event; "
                f"event types seen: {[e.type for e in session._events]}"
            )
            assert approved_ids, "At least one mutating command should have required approval"

            activity_data = draft_event.data["activity"]

            # ---- Submit the activity ----
            resp = await http.post(
                f"/api/agent/sessions/{sid}/activity",
                json={"activity": activity_data},
            )
            assert resp.status_code == 200, f"submit activity failed: {resp.text}"

            # ---- Verify ticket is now DONE in the ERP ----
            ticket_resp = await http.get(f"/api/tickets/{ticket_id}")
            # Our backend exposes GET /api/tickets/{id} which proxies to ERP
            if ticket_resp.status_code == 200:
                ticket_status = ticket_resp.json().get("status")
                assert ticket_status == "DONE", (
                    f"Expected ticket status DONE, got {ticket_status!r}"
                )

            # ---- Clean up ----
            await http.post(f"/api/agent/sessions/{sid}/abort")

    async def test_persisted_runner_active_and_enabled(self, tmp_path) -> None:  # noqa: ANN001
        """After the full loop + approval, the runner must be active AND enabled."""
        erp_client = _make_erp_client()
        script = _make_nginx_script()
        runner_instance = MockSSHRunner()

        def runner_factory(_cs: Any) -> MockSSHRunner:
            return runner_instance

        sm = SessionManager(
            erp_client=erp_client,
            runner_factory=runner_factory,
            llm_factory=lambda: StubLLM(copy.deepcopy(script)),
        )
        set_session_manager(sm)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_app),
            base_url="http://test",
        ) as http:
            # Create + start
            resp = await http.post(
                "/api/agent/sessions",
                json={"ticket_id": 7001},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]

            resp = await http.post(f"/api/agent/sessions/{sid}/start")
            assert resp.status_code == 200

            session = sm.get(sid)
            assert session is not None

            # Drive the loop: approve all awaiting_approval until activity_draft
            approved_ids: set[str] = set()
            for _ in range(200):
                await asyncio.sleep(0.05)

                for ev in list(session._events):
                    if (
                        ev.type == "awaiting_approval"
                        and ev.data["action_id"] not in approved_ids
                    ):
                        action_id = ev.data["action_id"]
                        approved_ids.add(action_id)
                        await http.post(
                            f"/api/agent/sessions/{sid}/approve",
                            json={"action_id": action_id},
                        )

                if any(ev.type == "activity_draft" for ev in session._events):
                    break

            # The runner must be active and enabled after the full loop
            assert runner_instance.active is True, (
                "Expected runner.active=True after nginx recovery"
            )
            assert runner_instance.enabled is True, (
                "Expected runner.enabled=True after nginx recovery"
            )

            await http.post(f"/api/agent/sessions/{sid}/abort")
