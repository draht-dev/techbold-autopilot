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


class ManualReportRequest(BaseModel):
    """Technician-submitted activity report, bypassing the agent draft flow."""

    summary: str = ""
    root_cause: str = ""
    actions_taken: str = ""
    commands_summary: str = ""
    validation_result: str = ""
    description: str = ""
    # "fixed" marks the ticket DONE; anything else returns it to PENDING.
    outcome: Literal["fixed", "pending"] = "pending"


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
    REPRODUCING = "REPRODUCING"
    INVESTIGATING = "INVESTIGATING"
    HYPOTHESES = "HYPOTHESES"
    CHECK = "CHECK"
    FIX_PROPOSE = "FIX_PROPOSE"
    APPLY = "APPLY"
    VALIDATE = "VALIDATE"
    PERSIST_VERIFY = "PERSIST_VERIFY"
    AWAITING_INPUT = "AWAITING_INPUT"
    ACTIVITY_DRAFT = "ACTIVITY_DRAFT"
    SUBMITTING = "SUBMITTING"
    # A plain interactive SSH session opened by the technician with NO agent loop
    # running (e.g. to inspect a ticket's machine after it is resolved).
    SHELL = "SHELL"
    DONE = "DONE"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class HypothesisComment(BaseModel):
    """A free-text note a technician attaches to a hypothesis."""

    author: str = "technician"
    text: str
    ts: str = Field(default_factory=utcnow_iso)


class Hypothesis(BaseModel):
    id: str
    rank: int
    title: str
    reasoning: str = ""
    evidence: str = ""
    # A hypothesis is NOT limited to a single bash command: it carries an ordered
    # list of read-only checks the agent runs (and aggregates) during the CHECK phase.
    checks: list[str] = Field(default_factory=list)
    # Relative likelihood as a PERCENTAGE (0-100), normalised across the ranked set
    # so the technician sees how confident the agent is, not a fixed raw score.
    likelihood: Optional[float] = None
    # Where the hypothesis came from: the agent, or a technician who typed their own.
    source: Literal["agent", "technician"] = "agent"
    # Technician comments steer the agent: they are fed into the check / re-rank context.
    comments: list[HypothesisComment] = Field(default_factory=list)
    status: Literal["open", "checking", "confirmed", "rejected"] = "open"

    @property
    def proposed_check(self) -> str:
        """First check command (back-compat convenience for single-command callers)."""
        return self.checks[0] if self.checks else ""


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
    DECISION_REQUEST = "decision.request"
    DECISION_RESOLVED = "decision.resolved"
    AGENT_MESSAGE = "agent.message"
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
