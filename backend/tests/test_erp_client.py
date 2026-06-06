"""Phoenix client tests against the in-process mock ERP."""
import httpx
import pytest

from app.erp import PhoenixClient, PhoenixError
from app.mock.phoenix import create_app
from app.models import ActivityCreate, TicketStatus


def make_client(token: str = "valid-team-token") -> PhoenixClient:
    transport = httpx.ASGITransport(app=create_app())
    return PhoenixClient("http://testserver", token, transport=transport)


async def test_get_me():
    client = make_client()
    me = await client.get_me()
    assert me.username == "m.mustermann"
    await client.aclose()


async def test_list_tickets_sort_and_filter():
    client = make_client()
    tickets = await client.list_tickets(sort="priority")
    assert len(tickets) >= 2
    high_only = await client.list_tickets(priority="high")
    assert all(t.priority == "high" for t in high_only)
    await client.aclose()


async def test_customer_system():
    client = make_client()
    cs = await client.get_customer_system(7001)
    assert cs.system.ip and cs.system.port == 22
    await client.aclose()


async def test_unauthorized_raises_401():
    client = make_client("invalid-token")
    with pytest.raises(PhoenixError) as info:
        await client.get_me()
    assert info.value.status_code == 401
    await client.aclose()


async def test_not_found_raises_404():
    client = make_client()
    with pytest.raises(PhoenixError) as info:
        await client.get_ticket(999999)
    assert info.value.status_code == 404
    await client.aclose()


async def test_create_activity_and_status():
    client = make_client()
    activity = ActivityCreate(
        ticket_id=7001,
        start_datetime="2026-06-07T10:00:00Z",
        end_datetime="2026-06-07T10:05:00Z",
        summary="Restored the status API.",
        root_cause="nginx upstream was down.",
        actions_taken="Restarted and enabled the unit.",
        commands_summary="systemctl enable --now nginx",
        validation_result="curl localhost returned 200.",
    )
    created = await client.create_activity(activity)
    assert created.id and created.ticket_id == 7001
    updated = await client.set_status(7001, TicketStatus.DONE)
    assert updated.status == TicketStatus.DONE
    await client.aclose()
