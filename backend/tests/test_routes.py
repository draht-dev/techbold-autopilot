"""HTTP-boundary tests for the run-discovery routes and the awaited create_run.

The lifecycle logic is unit-tested in test_run_lifecycle.py; these exercise the
FastAPI wiring users actually hit: GET /api/runs, GET /api/tickets/{id}/active-run,
the DONE guards (agent re-run blocked, plain SSH still allowed), the resolution
endpoint, and that start_run awaits create_run and supersedes through the route.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.models import CustomerSystem, RunPhase, SystemInfo, Ticket, TicketStatus
from app.runs.manager import RunManager


class FakeERP:
    def __init__(self):
        # Per-ticket status, mutable so a test can mark a ticket DONE.
        self.status = TicketStatus.OPEN

    async def get_ticket(self, ticket_id):
        return Ticket(
            id=ticket_id, title="t", description="d", priority="high",
            status=self.status, customer_id=1, customer_name="C",
        )

    async def get_customer_system(self, ticket_id):
        return CustomerSystem(
            ticket_id=ticket_id, customer_id=1,
            system=SystemInfo(ip="10.0.0.5", port=22, username="azureuser", os="Ubuntu"),
        )

    async def aclose(self):  # lifespan shutdown calls this on app.state.erp
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    # The spawned agent / shell tasks must not actually open SSH or call an LLM: just
    # mark the run in-flight and stay alive so it shows up as active.
    async def fake_run_agent(run, llm):
        run.set_phase(RunPhase.RECON)
        await asyncio.Event().wait()

    async def fake_run_shell(run):
        run.set_phase(RunPhase.SHELL)
        await asyncio.Event().wait()

    monkeypatch.setattr("app.api.routes.run_agent", fake_run_agent)
    monkeypatch.setattr("app.api.routes.run_shell", fake_run_shell)
    erp = FakeERP()
    with TestClient(app) as c:
        mgr = RunManager(Settings(audit_dir=str(tmp_path)))
        mgr.erp = erp
        app.state.manager = mgr
        app.state.erp = erp
        c.erp = erp  # tests flip status through this
        c.mgr = mgr
        yield c
        # Tear down the in-flight runs we started (their tasks block forever).
        c.portal.call(mgr.shutdown)


def test_runs_endpoints_empty(client):
    assert client.get("/api/runs").json() == []
    assert client.get("/api/tickets/7001/active-run").json() is None


def test_start_run_then_discover(client):
    run_id = client.post("/api/runs", json={"ticket_id": 7001}).json()["run_id"]

    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["ticket_id"] == 7001
    assert runs[0]["active"] is True
    assert "events" not in runs[0]  # lightweight summary, not full snapshot

    active = client.get("/api/tickets/7001/active-run").json()
    assert active["id"] == run_id
    assert "events" not in active  # summary, not the full event log


def test_start_run_supersedes_through_route(client):
    a = client.post("/api/runs", json={"ticket_id": 7002}).json()["run_id"]
    b = client.post("/api/runs", json={"ticket_id": 7002}).json()["run_id"]
    assert a != b

    active = client.get("/api/tickets/7002/active-run").json()
    assert active["id"] == b  # newest supersedes the old

    active_for_ticket = [
        r for r in client.get("/api/runs").json() if r["active"] and r["ticket_id"] == 7002
    ]
    assert len(active_for_ticket) == 1  # only one live run, no orphan


# --------------------------------------------------------------------------- #
# DONE ticket: the agent must not be re-runnable, but plain SSH stays available.
# --------------------------------------------------------------------------- #
def test_start_run_blocked_when_ticket_done(client):
    client.erp.status = TicketStatus.DONE
    res = client.post("/api/runs", json={"ticket_id": 7001})
    assert res.status_code == 409
    assert "DONE" in res.json()["detail"]
    assert client.get("/api/runs").json() == []  # no run was created


def test_start_shell_run_allowed_when_ticket_done(client):
    client.erp.status = TicketStatus.DONE
    res = client.post("/api/runs/shell", json={"ticket_id": 7001})
    assert res.status_code == 200
    run_id = res.json()["run_id"]

    snap = client.get(f"/api/runs/{run_id}").json()
    assert snap["kind"] == "shell"  # a plain-SSH session, not an agent run


# --------------------------------------------------------------------------- #
# Resolution: a finished agent run's solution + log is retrievable per ticket.
# --------------------------------------------------------------------------- #
def test_resolution_null_when_no_resolved_run(client):
    assert client.get("/api/tickets/9999/resolution").json() is None


def test_resolution_returns_submitted_activity_and_log(client):
    run_id = client.post("/api/runs", json={"ticket_id": 7003}).json()["run_id"]
    run = client.mgr.get(run_id)
    run.info("Restarted the status API and verified it answers on :8080")
    run.submitted_activity = {
        "summary": "Restarted the status API service",
        "root_cause": "systemd unit was masked",
        "actions_taken": "unmasked + enabled + started",
        "commands_summary": "systemctl unmask ...",
        "validation_result": "public-test.sh passed",
        "description": "Full write-up",
    }
    run.outcome = "fixed"
    run.phase = RunPhase.DONE

    res = client.get("/api/tickets/7003/resolution").json()
    assert res["submitted_activity"]["summary"] == "Restarted the status API service"
    assert res["outcome"] == "fixed"
    assert any(
        e["type"] == "info" and "status API" in e.get("text", "") for e in res["events"]
    )


def test_resolution_survives_run_eviction(client):
    """The DONE ticket page still shows the solution + log after the run is gone."""
    run_id = client.post("/api/runs", json={"ticket_id": 7004}).json()["run_id"]
    run = client.mgr.get(run_id)
    run.info("Unmasked and started the unit; public-test.sh passed")
    run.submitted_activity = {"summary": "Unmasked the systemd unit"}
    run.outcome = "fixed"
    run.phase = RunPhase.DONE
    client.mgr.save_resolution(run)

    client.mgr.runs.clear()  # simulate GC eviction / backend restart

    res = client.get("/api/tickets/7004/resolution").json()
    assert res["submitted_activity"]["summary"] == "Unmasked the systemd unit"
    assert any("public-test.sh" in e.get("text", "") for e in res["events"])
