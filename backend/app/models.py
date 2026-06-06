"""Shared schemas (the frozen contracts).

These cover the Phoenix ERP entities we consume, the activity we write back, and
the internal run / event / hypothesis / approval shapes the frontend relies on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Phoenix ERP entities (mirror docs/phoenix-openapi.yaml)
# --------------------------------------------------------------------------- #
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


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    priority: str
    status: TicketStatus
    customer_id: int
    customer_name: str
    tags: list[str] = Field(default_factory=list)
    sla_due_at: Optional[str] = None
    created_at: Optional[str] = None


class SystemInfo(BaseModel):
    ip: str
    port: int = 22
    username: str = "azureuser"
    os: str = ""
    notes: Optional[str] = None


class CustomerSystem(BaseModel):
    ticket_id: int
    customer_id: int
    system: SystemInfo


class Customer(BaseModel):
    id: int
    company_name: str
    firstname: str = ""
    lastname: str = ""
    system: SystemInfo


class StatusUpdate(BaseModel):
    status: TicketStatus


class ActivityCreate(BaseModel):
    ticket_id: int
    start_datetime: str
    end_datetime: str
    description: str = ""
    summary: str = ""
    root_cause: str = ""
    actions_taken: str = ""
    commands_summary: str = ""
    validation_result: str = ""


class Activity(ActivityCreate):
    id: int
    team_id: int = 0
    team_name: str = ""
    employee_id: int = 0
    created_at: Optional[str] = None


# --------------------------------------------------------------------------- #
# Internal run / agent types
# --------------------------------------------------------------------------- #
class RunPhase(str, Enum):
    CONNECTING = "CONNECTING"
    RECON = "RECON"
    HYPOTHESES = "HYPOTHESES"
    CHECK = "CHECK"
    FIX_PROPOSE = "FIX_PROPOSE"
    APPLY = "APPLY"
    VALIDATE = "VALIDATE"
    PERSIST_VERIFY = "PERSIST_VERIFY"
    ACTIVITY_DRAFT = "ACTIVITY_DRAFT"
    SUBMITTING = "SUBMITTING"
    DONE = "DONE"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class Hypothesis(BaseModel):
    id: str
    rank: int
    title: str
    reasoning: str = ""
    evidence: str = ""
    proposed_check: str = ""
    likelihood: Optional[float] = None
    status: Literal["open", "checking", "confirmed", "rejected"] = "open"


class StartRunRequest(BaseModel):
    ticket_id: int
    auto_approve_reads: Optional[bool] = None


# --------------------------------------------------------------------------- #
# WebSocket event helpers (server -> client)
# --------------------------------------------------------------------------- #
class EventType(str, Enum):
    RUN_STATE = "run.state"
    COMMAND = "command.run"
    HYPOTHESES = "hypotheses"
    APPROVAL_REQUEST = "approval.request"
    APPROVAL_RESOLVED = "approval.resolved"
    VALIDATION = "validation.result"
    ACTIVITY_DRAFT = "activity.draft"
    ACTIVITY_SUBMITTED = "activity.submitted"
    TERM = "term.data"
    INFO = "info"
    ERROR = "error"


def make_event(type_: EventType | str, **data: Any) -> dict[str, Any]:
    return {
        "type": type_.value if isinstance(type_, EventType) else type_,
        "ts": utcnow_iso(),
        **data,
    }
