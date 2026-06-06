"""Offline mock of the Phoenix ERP.

Lets you develop and test the whole flow without Builder Base credentials.

Run standalone:  uvicorn app.mock.phoenix:app --port 8009
Then point .env: PHOENIX_API_BASE_URL=http://localhost:8009

It is intentionally permissive but mirrors the real contract (auth, 404, the
activity schema and the reset endpoint). Tests import ``app`` directly.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

INVALID_TOKENS = {"", "invalid", "invalid-token", "replace-with-your-team-token"}

_EMPLOYEE = {
    "id": 1001,
    "firstname": "Max",
    "lastname": "Mustermann",
    "username": "m.mustermann",
    "teamname": "Remote Support",
}

_SEED_TICKETS = [
    {
        "id": 7001,
        "title": "Status API intermittently unavailable",
        "description": "Customers report the status page returns 502 every few minutes.",
        "priority": "high",
        "status": "OPEN",
        "customer_id": 5001,
        "customer_name": "Nordlicht Logistik GmbH",
        "tags": ["web", "api"],
        "sla_due_at": None,
        "created_at": "2026-06-06T08:00:00Z",
    },
    {
        "id": 7002,
        "title": "Nightly backup job no longer runs",
        "description": "The customer noticed backups stopped appearing two days ago.",
        "priority": "medium",
        "status": "OPEN",
        "customer_id": 5002,
        "customer_name": "Alpentech AG",
        "tags": ["cron", "backup"],
        "sla_due_at": None,
        "created_at": "2026-06-05T20:00:00Z",
    },
]

_SYSTEMS = {
    5001: {"ip": "10.0.0.5", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Runs nginx + a Python status API."},
    5002: {"ip": "10.0.0.6", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Backups via cron + restic."},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app() -> FastAPI:
    app = FastAPI(title="Phoenix ERP Mock")
    state: dict[str, Any] = {
        "tickets": copy.deepcopy(_SEED_TICKETS),
        "activities": [],
        "activity_seq": 9000,
    }

    def auth(authorization: str = Header(default="")) -> str:
        token = authorization.removeprefix("Bearer ").strip()
        if token in INVALID_TOKENS:
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
        return token

    def _ticket(ticket_id: int) -> dict[str, Any]:
        for t in state["tickets"]:
            if t["id"] == ticket_id:
                return t
        raise HTTPException(status_code=404, detail="Ticket not found")

    @app.get("/api/v1/me")
    def get_me(_: str = Depends(auth)) -> Any:
        return _EMPLOYEE

    @app.get("/api/v1/me/tickets")
    def list_tickets(
        _: str = Depends(auth),
        status: str | None = None,
        priority: str | None = None,
        sort: str = "date",
    ) -> Any:
        items = [t for t in state["tickets"]]
        if status:
            items = [t for t in items if t["status"] == status]
        if priority:
            items = [t for t in items if t["priority"] == priority]
        if sort == "priority":
            order = {"high": 0, "medium": 1, "low": 2}
            items.sort(key=lambda t: order.get(t["priority"], 9))
        elif sort == "status":
            items.sort(key=lambda t: t["status"])
        else:
            items.sort(key=lambda t: t.get("created_at") or "", reverse=True)
        return items

    @app.get("/api/v1/tickets/{ticket_id}")
    def get_ticket(ticket_id: int, _: str = Depends(auth)) -> Any:
        return _ticket(ticket_id)

    @app.get("/api/v1/tickets/{ticket_id}/customer-system")
    def get_customer_system(ticket_id: int, _: str = Depends(auth)) -> Any:
        ticket = _ticket(ticket_id)
        system = _SYSTEMS.get(ticket["customer_id"])
        if system is None:
            raise HTTPException(status_code=404, detail="System not found")
        return {"ticket_id": ticket_id, "customer_id": ticket["customer_id"], "system": system}

    @app.get("/api/v1/customers/{customer_id}")
    def get_customer(customer_id: int, _: str = Depends(auth)) -> Any:
        system = _SYSTEMS.get(customer_id)
        if system is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"id": customer_id, "company_name": "Demo Customer", "firstname": "", "lastname": "", "system": system}

    @app.patch("/api/v1/tickets/{ticket_id}/status")
    def set_status(ticket_id: int, body: dict, _: str = Depends(auth)) -> Any:
        ticket = _ticket(ticket_id)
        new_status = body.get("status")
        if new_status not in ("OPEN", "PENDING", "DONE"):
            raise HTTPException(status_code=422, detail="invalid status")
        ticket["status"] = new_status
        return ticket

    @app.post("/api/v1/activities/create", status_code=201)
    def create_activity(body: dict, _: str = Depends(auth)) -> Any:
        if "ticket_id" not in body:
            raise HTTPException(status_code=422, detail="ticket_id required")
        state["activity_seq"] += 1
        activity = {
            "id": state["activity_seq"],
            "team_id": 1,
            "team_name": _EMPLOYEE["teamname"],
            "employee_id": _EMPLOYEE["id"],
            "created_at": _now(),
            **body,
        }
        state["activities"].append(activity)
        return activity

    @app.post("/api/v1/me/reset")
    def reset(_: str = Depends(auth)) -> Any:
        state["tickets"] = copy.deepcopy(_SEED_TICKETS)
        state["activities"] = []
        return {"message": "reset complete", "detail": {"vms": "reboot requested"}}

    return app


app = create_app()
