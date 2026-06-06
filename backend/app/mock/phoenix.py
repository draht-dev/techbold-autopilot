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

# Ticket descriptions are written in MARKDOWN — the technician ticket view renders
# them (headings, lists, code, bold), so they read like a real customer report.
_SEED_TICKETS = [
    {
        "id": 7001,
        "title": "Public website returning 502 Bad Gateway",
        "description": (
            "## Public website down — 502 Bad Gateway\n\n"
            "Our public website has been returning **502 Bad Gateway** errors since early "
            "this morning. Customers **cannot access the online portal at all**.\n\n"
            "- **Impact:** all customer-facing traffic\n"
            "- **Started:** ~07:30 CET today\n"
            "- **URL:** `https://portal.nordlicht-logistik.example`\n\n"
            "### What we see\n"
            "```\n502 Bad Gateway\nnginx/1.18.0 (Ubuntu)\n```\n\n"
            "Please restore service as soon as possible."
        ),
        "priority": "high",
        "status": "OPEN",
        "customer_id": 5001,
        "customer_name": "Nordlicht Logistik GmbH",
        "tags": ["web", "nginx", "502", "customer-facing"],
        "sla_due_at": "2026-06-06T12:00:00Z",
        "created_at": "2026-06-06T07:35:00Z",
    },
    {
        "id": 7002,
        "title": "Internal status API intermittently unavailable",
        "description": (
            "## Fleet status API flapping\n\n"
            "Our internal **fleet status API** intermittently returns `503` or times out "
            "since yesterday evening. Operations staff cannot reliably check vehicle status.\n\n"
            "The problem **comes and goes every few minutes**.\n\n"
            "| Symptom | Frequency |\n"
            "| --- | --- |\n"
            "| HTTP 503 | every few minutes |\n"
            "| Request timeout | occasionally |\n\n"
            "> Started ~19:00 CET yesterday. No deploy happened around that time."
        ),
        "priority": "high",
        "status": "OPEN",
        "customer_id": 5001,
        "customer_name": "Nordlicht Logistik GmbH",
        "tags": ["api", "backend", "intermittent", "503"],
        "sla_due_at": "2026-06-06T14:00:00Z",
        "created_at": "2026-06-05T19:05:00Z",
    },
    {
        "id": 7003,
        "title": "Booking portal unreachable for customers",
        "description": (
            "## Booking portal unreachable\n\n"
            "Customers cannot reach our online **booking portal** — they get a connection "
            "timeout or a TLS warning in the browser.\n\n"
            "1. Open `https://booking.alpentech.example`\n"
            "2. Page hangs, then shows a security warning\n\n"
            "This started **after the scheduled maintenance window last night**."
        ),
        "priority": "high",
        "status": "OPEN",
        "customer_id": 5002,
        "customer_name": "Alpentech AG",
        "tags": ["booking", "tls", "portal", "customer-facing"],
        "sla_due_at": "2026-06-06T13:00:00Z",
        "created_at": "2026-06-06T06:10:00Z",
    },
    {
        "id": 7004,
        "title": "Nightly backup job failing — cannot write to data volume",
        "description": (
            "## Nightly backup failing\n\n"
            "The automated **nightly backup** has failed for the past three nights. The "
            "backup report says it *cannot write to the data volume*.\n\n"
            "- No successful backup since **June 3rd**\n"
            "- Error: `No space left on device` (from the job log)\n\n"
            "Please check the backup volume and restore the nightly job."
        ),
        "priority": "medium",
        "status": "OPEN",
        "customer_id": 5003,
        "customer_name": "Donau Handel GmbH",
        "tags": ["backup", "storage", "disk", "nightly-job"],
        "sla_due_at": None,
        "created_at": "2026-06-06T05:00:00Z",
    },
    {
        "id": 7005,
        "title": "Login page showing TLS certificate warning",
        "description": (
            "## TLS certificate warning on login\n\n"
            "Users logging in to the **employee portal** see a browser TLS/SSL certificate "
            "warning. Security-conscious employees **refuse to proceed**.\n\n"
            "- Affects **all browsers**\n"
            "- Warning mentions the certificate has **expired**\n"
        ),
        "priority": "medium",
        "status": "OPEN",
        "customer_id": 5004,
        "customer_name": "Rheingold Versicherung GmbH",
        "tags": ["tls", "certificate", "login", "security"],
        "sla_due_at": None,
        "created_at": "2026-06-05T22:30:00Z",
    },
    {
        "id": 7006,
        "title": "Database-backed reporting service not starting",
        "description": (
            "## Monthly reporting service won't start\n\n"
            "The monthly **reporting service** fails to start and produces no reports. The "
            "scheduled monthly report was **missing from the shared drive** this morning.\n\n"
            "The service appears to **crash immediately on startup**.\n\n"
            "```\nreporting.service: Failed with result 'exit-code'.\n```"
        ),
        "priority": "low",
        "status": "OPEN",
        "customer_id": 5002,
        "customer_name": "Alpentech AG",
        "tags": ["reporting", "database", "service-crash"],
        "sla_due_at": None,
        "created_at": "2026-06-06T08:15:00Z",
    },
]

# Systems are keyed by TICKET id so each incident gets its own VM (two tickets for
# the same customer can run on different hosts, mirroring the real fixtures).
_SYSTEMS = {
    7001: {"ip": "203.0.113.11", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Public web tier: nginx reverse proxy (80/443) -> gunicorn app on :8000. App user webapp, app under /srv/portal; static served from /srv/portal/static."},
    7002: {"ip": "203.0.113.12", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Fleet status API: FastAPI under systemd unit 'fleet-api.service' on :8080. App user fleetapi, /opt/fleet-api. PostgreSQL 14 on localhost:5432."},
    7003: {"ip": "203.0.113.21", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Booking portal: nginx (443, TLS) -> Node.js Express on :3000. App user booking, /srv/booking. TLS certs under /etc/ssl/booking/."},
    7004: {"ip": "10.0.1.50", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Backup server: systemd timer 'nightly-backup.timer' -> 'nightly-backup.service'. Destination /mnt/backupvol. Script /opt/backup/run_backup.sh, user backupagent."},
    7005: {"ip": "203.0.113.41", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Employee login portal: nginx (443) TLS termination, cert+key under /etc/nginx/ssl/. Backend on :4000 via 'employee-portal.service', user empportal."},
    7006: {"ip": "203.0.113.22", "port": 22, "username": "azureuser", "os": "Ubuntu 22.04 LTS", "notes": "Reporting service: Python service under systemd unit 'reporting.service'. PostgreSQL 14 on localhost:5432 (db 'reports'), user reportinguser, output /srv/reports/monthly."},
}

# Customer -> a representative system (for the get_customer endpoint).
_CUSTOMER_SYSTEMS = {5001: 7001, 5002: 7003, 5003: 7004, 5004: 7005}


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
        system = _SYSTEMS.get(ticket_id)
        if system is None:
            raise HTTPException(status_code=404, detail="System not found")
        return {"ticket_id": ticket_id, "customer_id": ticket["customer_id"], "system": system}

    @app.get("/api/v1/customers/{customer_id}")
    def get_customer(customer_id: int, _: str = Depends(auth)) -> Any:
        system = _SYSTEMS.get(_CUSTOMER_SYSTEMS.get(customer_id, -1))
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
