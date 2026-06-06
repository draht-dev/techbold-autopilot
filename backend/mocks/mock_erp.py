"""Standalone mock Phoenix ERP server for offline development and demo.

Run from the backend/ directory:
    uvicorn mocks.mock_erp:app --port 9000 --reload

Or directly:
    python -m mocks.mock_erp

Any non-empty Bearer token is accepted — this is a mock server.
The real Phoenix ERP requires a team-specific token; swap PHOENIX_API_BASE_URL
and PHOENIX_API_TOKEN in .env to point at the real ERP when available.

Endpoints implemented (all require Authorization: Bearer <token>):
    GET  /api/v1/me
    GET  /api/v1/me/tickets?status=&priority=&sort=
    GET  /api/v1/tickets/{ticket_id}
    GET  /api/v1/tickets/{ticket_id}/customer-system
    GET  /api/v1/customers/{customer_id}
    PATCH /api/v1/tickets/{ticket_id}/status
    POST /api/v1/activities/create
    POST /api/v1/me/reset
"""

import json
import copy
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_json(name: str) -> Any:
    with open(_FIXTURES_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


_SEED_TICKETS: list[dict] = _load_json("tickets.json")
_SEED_CUSTOMER_SYSTEMS: dict[str, dict] = _load_json("customer_systems.json")
_SEED_CUSTOMERS: dict[str, dict] = _load_json("customers.json")

# Mutable in-memory state (reset via POST /api/v1/me/reset)
_tickets: list[dict] = copy.deepcopy(_SEED_TICKETS)
_activities: list[dict] = []
_activity_id_counter: int = 1

# ---------------------------------------------------------------------------
# Pydantic models — defined inline; matches phoenix-openapi.yaml schemas exactly
# ---------------------------------------------------------------------------


class TicketStatus(str, Enum):
    OPEN = "OPEN"
    PENDING = "PENDING"
    DONE = "DONE"


class Employee(BaseModel):
    id: int
    firstname: str
    lastname: str
    username: str
    teamname: str


class SystemInfo(BaseModel):
    ip: str
    port: int
    username: str
    os: str
    notes: Optional[str] = None


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    priority: str
    status: TicketStatus
    customer_id: int
    customer_name: str
    tags: Optional[list[str]] = None
    sla_due_at: Optional[str] = None
    created_at: Optional[str] = None


class CustomerSystem(BaseModel):
    ticket_id: int
    customer_id: int
    system: SystemInfo


class Customer(BaseModel):
    id: int
    company_name: str
    firstname: str
    lastname: str
    system: SystemInfo


class StatusUpdate(BaseModel):
    status: TicketStatus


class ActivityCreate(BaseModel):
    ticket_id: int
    start_datetime: str
    end_datetime: str
    description: Optional[str] = None
    summary: Optional[str] = None
    root_cause: Optional[str] = None
    actions_taken: Optional[str] = None
    commands_summary: Optional[str] = None
    validation_result: Optional[str] = None


class Activity(BaseModel):
    id: int
    team_id: int
    team_name: str
    employee_id: int
    ticket_id: int
    start_datetime: str
    end_datetime: str
    description: Optional[str] = None
    summary: Optional[str] = None
    root_cause: Optional[str] = None
    actions_taken: Optional[str] = None
    commands_summary: Optional[str] = None
    validation_result: Optional[str] = None
    created_at: Optional[str] = None


class SimpleMessage(BaseModel):
    message: str
    detail: Optional[dict] = None


# ---------------------------------------------------------------------------
# Static technician identity
# ---------------------------------------------------------------------------

_TECHNICIAN = Employee(
    id=1001,
    firstname="Max",
    lastname="Mustermann",
    username="m.mustermann",
    teamname="Remote Support",
)

# ---------------------------------------------------------------------------
# FastAPI app + Bearer auth dependency
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Phoenix ERP Mock",
    version="1.0.0",
    description=(
        "Standalone mock of the Phoenix ERP API. "
        "Accepts any non-empty Bearer token. "
        "Run with: uvicorn mocks.mock_erp:app --port 9000"
    ),
)

_bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    """Dependency: require a non-empty Bearer token; return it on success."""
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
        )
    return credentials.credentials


# ---------------------------------------------------------------------------
# Helper: look up a ticket by id from the mutable list
# ---------------------------------------------------------------------------


def _find_ticket(ticket_id: int) -> dict:
    for t in _tickets:
        if t["id"] == ticket_id:
            return t
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Ticket {ticket_id} not found",
    )


# ---------------------------------------------------------------------------
# Priority sort order helper
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_STATUS_ORDER = {"OPEN": 0, "PENDING": 1, "DONE": 2}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/v1/me", response_model=Employee)
def get_me(_token: str = Depends(require_bearer)) -> Employee:
    """Return the logged-in technician's identity."""
    return _TECHNICIAN


@app.get("/api/v1/me/tickets", response_model=list[Ticket])
def list_my_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    sort: Optional[str] = "date",
    _token: str = Depends(require_bearer),
) -> list[Ticket]:
    """Return all assigned tickets with optional filtering and sorting."""
    results = list(_tickets)

    # Filter
    if status:
        results = [t for t in results if t["status"] == status.upper()]
    if priority:
        results = [t for t in results if t["priority"] == priority.lower()]

    # Sort
    sort_key = sort or "date"
    if sort_key == "date":
        results.sort(
            key=lambda t: t.get("created_at") or "",
            reverse=True,
        )
    elif sort_key == "priority":
        results.sort(key=lambda t: _PRIORITY_ORDER.get(t["priority"], 99))
    elif sort_key == "status":
        results.sort(key=lambda t: _STATUS_ORDER.get(t["status"], 99))

    return [Ticket(**t) for t in results]


@app.get("/api/v1/tickets/{ticket_id}", response_model=Ticket)
def get_ticket(
    ticket_id: int,
    _token: str = Depends(require_bearer),
) -> Ticket:
    """Return a single ticket by ID."""
    return Ticket(**_find_ticket(ticket_id))


@app.get(
    "/api/v1/tickets/{ticket_id}/customer-system",
    response_model=CustomerSystem,
)
def get_customer_system(
    ticket_id: int,
    _token: str = Depends(require_bearer),
) -> CustomerSystem:
    """Return the SSH target (customer system) for a ticket."""
    entry = _SEED_CUSTOMER_SYSTEMS.get(str(ticket_id))
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No customer system found for ticket {ticket_id}",
        )
    return CustomerSystem(**entry)


@app.get("/api/v1/customers/{customer_id}", response_model=Customer)
def get_customer(
    customer_id: int,
    _token: str = Depends(require_bearer),
) -> Customer:
    """Return customer information by customer ID."""
    entry = _SEED_CUSTOMERS.get(str(customer_id))
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer {customer_id} not found",
        )
    return Customer(**entry)


@app.patch("/api/v1/tickets/{ticket_id}/status", response_model=Ticket)
def set_ticket_status(
    ticket_id: int,
    body: StatusUpdate,
    _token: str = Depends(require_bearer),
) -> Ticket:
    """Update a ticket's status. Returns the updated ticket."""
    ticket = _find_ticket(ticket_id)
    ticket["status"] = body.status.value
    return Ticket(**ticket)


@app.post(
    "/api/v1/activities/create",
    response_model=Activity,
    status_code=status.HTTP_201_CREATED,
)
def create_activity(
    body: ActivityCreate,
    _token: str = Depends(require_bearer),
) -> Activity:
    """Create an activity record. ticket_id must be one of the assigned tickets."""
    global _activity_id_counter

    # Validate that the ticket exists and is assigned
    _find_ticket(body.ticket_id)

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    activity = Activity(
        id=_activity_id_counter,
        team_id=1,
        team_name=_TECHNICIAN.teamname,
        employee_id=_TECHNICIAN.id,
        ticket_id=body.ticket_id,
        start_datetime=body.start_datetime,
        end_datetime=body.end_datetime,
        description=body.description,
        summary=body.summary,
        root_cause=body.root_cause,
        actions_taken=body.actions_taken,
        commands_summary=body.commands_summary,
        validation_result=body.validation_result,
        created_at=now_iso,
    )
    _activities.append(activity.model_dump())
    _activity_id_counter += 1
    return activity


@app.post("/api/v1/me/reset", response_model=SimpleMessage)
def reset_me(_token: str = Depends(require_bearer)) -> SimpleMessage:
    """Clear stored activities and reset ticket statuses to seed values."""
    global _tickets, _activities, _activity_id_counter

    cleared_activities = len(_activities)
    _activities = []
    _activity_id_counter = 1
    _tickets = copy.deepcopy(_SEED_TICKETS)

    return SimpleMessage(
        message="Reset complete. Activities cleared and ticket statuses restored to seed values.",
        detail={
            "activities_cleared": cleared_activities,
            "tickets_reset": len(_tickets),
        },
    )


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
