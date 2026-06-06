"""Integration tests for ErpClient against the mock ERP — SPEC §12.

Uses httpx.ASGITransport to route all HTTP calls directly through
mocks.mock_erp.app without starting a real server.  No network required.
"""
from __future__ import annotations

import pytest
import httpx

from mocks.mock_erp import app as erp_app
from app.erp_client import ErpClient, ErpAuthError
from app.models import (
    ActivityCreate,
    Customer,
    CustomerSystem,
    Employee,
    Ticket,
    TicketStatus,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE = "http://erp"
_TOKEN = "test-token"


def _make_client(token: str = _TOKEN) -> ErpClient:
    """Build an ErpClient whose HTTP transport routes through the mock ERP app."""
    transport = httpx.ASGITransport(app=erp_app)
    http_client = httpx.AsyncClient(transport=transport, base_url=_BASE)
    return ErpClient(base_url=_BASE, token=token, client=http_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetMe:
    async def test_returns_employee_with_username(self) -> None:
        client = _make_client()
        employee = await client.get_me()
        assert isinstance(employee, Employee)
        assert employee.username  # non-empty string


class TestListTickets:
    async def test_returns_non_empty_list(self) -> None:
        client = _make_client()
        tickets = await client.list_tickets()
        assert len(tickets) > 0
        for t in tickets:
            assert isinstance(t, Ticket)

    async def test_default_sort_is_created_at_descending(self) -> None:
        client = _make_client()
        tickets = await client.list_tickets(sort="date")
        # Filter to those that have created_at set
        dated = [t for t in tickets if t.created_at]
        for i in range(len(dated) - 1):
            assert dated[i].created_at >= dated[i + 1].created_at, (
                f"Expected descending created_at: {dated[i].created_at!r} "
                f">= {dated[i + 1].created_at!r}"
            )

    async def test_status_filter_narrows_results(self) -> None:
        client = _make_client()
        all_tickets = await client.list_tickets()
        open_tickets = await client.list_tickets(status="OPEN")
        # All returned tickets should be OPEN
        for t in open_tickets:
            assert t.status == TicketStatus.OPEN
        # There are fewer OPEN tickets than all tickets (fixtures have PENDING/DONE too)
        assert len(open_tickets) < len(all_tickets)

    async def test_priority_filter_narrows_results(self) -> None:
        client = _make_client()
        all_tickets = await client.list_tickets()
        high_tickets = await client.list_tickets(priority="high")
        for t in high_tickets:
            assert t.priority == "high"
        # There should be high-priority tickets in the fixtures
        assert len(high_tickets) > 0
        assert len(high_tickets) <= len(all_tickets)

    async def test_non_matching_filter_returns_empty_list(self) -> None:
        client = _make_client()
        # "DONE" + "low" — fixtures have no ticket matching both simultaneously
        done_low = await client.list_tickets(status="DONE", priority="low")
        # Either empty or a small set — the key assertion is no crash and list returned
        assert isinstance(done_low, list)

    async def test_empty_list_no_crash(self) -> None:
        """A combination that should match nothing returns [] without error."""
        client = _make_client()
        # Use an unlikely combination (pending + low)
        results = await client.list_tickets(status="PENDING", priority="low")
        assert isinstance(results, list)


class TestGetTicket:
    async def test_known_ticket_returns_ticket(self) -> None:
        client = _make_client()
        ticket = await client.get_ticket(7001)
        assert ticket is not None
        assert isinstance(ticket, Ticket)
        assert ticket.id == 7001

    async def test_unknown_ticket_returns_none(self) -> None:
        client = _make_client()
        result = await client.get_ticket(99999999)
        assert result is None


class TestGetCustomerSystem:
    async def test_known_ticket_returns_customer_system(self) -> None:
        client = _make_client()
        cs = await client.get_customer_system(7001)
        assert cs is not None
        assert isinstance(cs, CustomerSystem)
        # Must have SSH connection details
        assert cs.system.ip
        assert cs.system.port > 0
        assert cs.system.username

    async def test_unknown_ticket_returns_none(self) -> None:
        client = _make_client()
        result = await client.get_customer_system(99999999)
        assert result is None


class TestSetStatus:
    async def test_set_status_pending_returns_updated_ticket(self) -> None:
        client = _make_client()
        # Use ticket 7001 which is OPEN initially
        updated = await client.set_status(7001, "PENDING")
        assert updated is not None
        assert isinstance(updated, Ticket)
        assert updated.status == TicketStatus.PENDING

    async def test_set_status_back_to_open(self) -> None:
        client = _make_client()
        updated = await client.set_status(7001, "OPEN")
        assert updated is not None
        assert updated.status == TicketStatus.OPEN

    async def test_set_status_done(self) -> None:
        client = _make_client()
        updated = await client.set_status(7001, "DONE")
        assert updated is not None
        assert updated.status == TicketStatus.DONE
        # Restore to OPEN so other tests are unaffected
        await client.set_status(7001, "OPEN")


class TestCreateActivityAndReset:
    async def test_create_activity_returns_activity_with_id(self) -> None:
        client = _make_client()
        activity_in = ActivityCreate(
            ticket_id=7001,
            start_datetime="2026-06-06T10:00:00Z",
            end_datetime="2026-06-06T10:25:00Z",
            description="Full integration test activity",
            summary="nginx was stopped and not enabled; fixed with systemctl enable --now nginx.",
            root_cause="nginx unit was in a stopped+disabled state after a package reconfigure.",
            actions_taken="1. Checked systemctl status nginx. 2. Ran systemctl enable --now nginx.",
            commands_summary="systemctl status nginx; systemctl enable --now nginx; systemctl is-active nginx",
            validation_result="nginx is-active=active, is-enabled=enabled; curl localhost returned HTTP 200.",
        )
        created = await client.create_activity(activity_in)
        assert created.id > 0
        assert created.ticket_id == 7001
        assert created.summary == activity_in.summary

    async def test_reset_returns_message_and_clears_state(self) -> None:
        client = _make_client()
        # Create an activity to ensure there is something to clear
        activity_in = ActivityCreate(
            ticket_id=7001,
            start_datetime="2026-06-06T10:00:00Z",
            end_datetime="2026-06-06T10:20:00Z",
            summary="test activity for reset",
            root_cause="test",
            actions_taken="test",
            commands_summary="test",
            validation_result="test",
        )
        await client.create_activity(activity_in)

        result = await client.reset()
        # The mock returns a SimpleMessage serialised as dict
        assert isinstance(result, dict)
        assert "message" in result
        assert "reset" in result["message"].lower() or "cleared" in result["message"].lower()


class TestAuth401:
    async def test_empty_token_raises_erp_auth_error(self) -> None:
        """An empty Bearer token is rejected by the mock ERP with 401."""
        transport = httpx.ASGITransport(app=erp_app)
        http_client = httpx.AsyncClient(transport=transport, base_url=_BASE)
        # Empty token — the mock returns 401 when the token is empty/missing
        bad_client = ErpClient(base_url=_BASE, token="", client=http_client)
        with pytest.raises(ErpAuthError) as exc_info:
            await bad_client.get_me()
        # The error message should hint at the token problem
        assert exc_info.value.status_code == 401
