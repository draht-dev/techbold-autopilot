"""Pydantic v2 models for the AI Service Desk Autopilot.

Covers (in order):
  - ERP entities   — field names match phoenix-openapi.yaml exactly
  - Agent contract — SPEC §7.2 (strict JSON the LLM returns)
  - Execution/audit — CommandResult, Approval, AuditEntry (SPEC §6)
  - SSE events     — SPEC §9
  - Backend API request/response bodies — SPEC §8

All models use ``extra="ignore"`` so unexpected ERP fields do not crash parsing.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared model config — ignore unknown fields from the ERP
# ---------------------------------------------------------------------------
_IGNORE_EXTRA = ConfigDict(extra="ignore")


# ===========================================================================
# ERP entities (phoenix-openapi.yaml)
# ===========================================================================


class TicketStatus(str, Enum):
    """Possible lifecycle states of a ticket (SPEC §2)."""

    OPEN = "OPEN"
    PENDING = "PENDING"
    DONE = "DONE"


class Employee(BaseModel):
    """Technician returned by GET /api/v1/me."""

    model_config = _IGNORE_EXTRA

    id: int
    firstname: str
    lastname: str
    username: str
    teamname: str


class Ticket(BaseModel):
    """Single ticket as returned by the ERP (GET /api/v1/tickets/{id})."""

    model_config = _IGNORE_EXTRA

    id: int
    title: str
    description: str
    priority: str
    status: TicketStatus
    customer_id: int
    customer_name: str
    tags: list[str] = []
    sla_due_at: Optional[str] = None
    created_at: Optional[str] = None


class SystemInfo(BaseModel):
    """SSH-target info embedded in CustomerSystem and Customer."""

    model_config = _IGNORE_EXTRA

    ip: str
    port: int
    username: str
    os: str
    notes: str = ""


class CustomerSystem(BaseModel):
    """Response from GET /api/v1/tickets/{id}/customer-system."""

    model_config = _IGNORE_EXTRA

    ticket_id: int
    customer_id: int
    system: SystemInfo


class Customer(BaseModel):
    """Response from GET /api/v1/customers/{id}."""

    model_config = _IGNORE_EXTRA

    id: int
    company_name: str
    firstname: str
    lastname: str
    system: SystemInfo


class StatusUpdate(BaseModel):
    """Request body for PATCH /api/v1/tickets/{id}/status."""

    model_config = _IGNORE_EXTRA

    status: TicketStatus


class ActivityCreate(BaseModel):
    """Request body for POST /api/v1/activities/create (SPEC §2.1).

    ``ticket_id``, ``start_datetime``, and ``end_datetime`` are the only ERP-
    required fields.  The remaining fields are the GRADED documentation fields;
    they default to "" so the activity generator always populates them.
    """

    model_config = _IGNORE_EXTRA

    ticket_id: int
    start_datetime: str
    end_datetime: str
    description: str = ""
    summary: str = ""
    root_cause: str = ""
    actions_taken: str = ""
    commands_summary: str = ""
    validation_result: str = ""


class Activity(BaseModel):
    """Response from POST /api/v1/activities/create."""

    model_config = _IGNORE_EXTRA

    id: int
    team_id: int
    team_name: str
    employee_id: int
    ticket_id: int
    start_datetime: str
    end_datetime: str
    description: str = ""
    summary: str = ""
    root_cause: str = ""
    actions_taken: str = ""
    commands_summary: str = ""
    validation_result: str = ""
    created_at: Optional[str] = None


# ===========================================================================
# Agent interaction contract (SPEC §7.2)
# ===========================================================================


class Phase(str, Enum):
    """Orchestrator state-machine phases (SPEC §7.1)."""

    TRIAGE = "triage"
    DIAGNOSE = "diagnose"
    PROPOSE_FIX = "propose_fix"
    APPLY = "apply"
    VALIDATE = "validate"
    PERSIST_CHECK = "persist_check"
    DOCUMENT = "document"
    SUBMIT = "submit"


class Hypothesis(BaseModel):
    """One ranked hypothesis emitted by the LLM (SPEC §7.2)."""

    model_config = _IGNORE_EXTRA

    cause: str
    evidence: str
    confidence: float


class ProposedCommand(BaseModel):
    """A command the LLM proposes for execution (SPEC §7.2)."""

    model_config = _IGNORE_EXTRA

    command: str
    purpose: str
    mutating: bool = False
    expected_effect: str = ""
    rollback: str = ""


class FinalReport(BaseModel):
    """The ``final`` block the LLM supplies when ``ready_to_finish`` (SPEC §7.2)."""

    model_config = _IGNORE_EXTRA

    root_cause: str
    summary: str
    actions_taken: str
    commands_summary: str
    validation_result: str


class AgentResponse(BaseModel):
    """Complete JSON response from the LLM (SPEC §7.2).

    The orchestrator validates this before acting on it; malformed responses
    are rejected and the model is re-prompted.
    """

    model_config = _IGNORE_EXTRA

    phase: str
    thought: str = ""
    hypotheses: list[Hypothesis] = []
    proposed_commands: list[ProposedCommand] = []
    ready_to_validate: bool = False
    ready_to_finish: bool = False
    final: Optional[FinalReport] = None


# ===========================================================================
# Execution + audit models (SPEC §5, §6)
# ===========================================================================


class CommandResult(BaseModel):
    """Outcome returned by the SSH runner after executing a command (SPEC §5)."""

    model_config = _IGNORE_EXTRA

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class Approval(str, Enum):
    """How a command's execution was authorised (SPEC §6 audit log)."""

    AUTO = "auto"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    HARD_BLOCK = "hard_block"


class AuditEntry(BaseModel):
    """One line in the append-only JSONL audit log (SPEC §6)."""

    model_config = _IGNORE_EXTRA

    ts: str
    phase: str
    command: str
    classification: str
    approval: str
    approved_by: Optional[str] = None
    exit_code: Optional[int] = None
    output_summary: str = ""
    agent_rationale: str = ""


# ===========================================================================
# SSE event models (SPEC §9)
# ===========================================================================


class EventType(str, Enum):
    """All event types emitted over the SSE stream (SPEC §9)."""

    PHASE_CHANGE = "phase_change"
    THOUGHT = "thought"
    HYPOTHESES = "hypotheses"
    PLAN = "plan"
    AWAITING_APPROVAL = "awaiting_approval"
    COMMAND_RESULT = "command_result"
    SAFETY_BLOCK = "safety_block"
    VALIDATION = "validation"
    PERSIST_CHECK = "persist_check"
    ACTIVITY_DRAFT = "activity_draft"
    DONE = "done"
    ERROR = "error"


class SSEEvent(BaseModel):
    """Generic SSE event envelope (SPEC §9).

    ``data`` carries a type-specific payload dict; callers populate it before
    sending.
    """

    model_config = _IGNORE_EXTRA

    type: str
    session_id: str
    ts: str
    data: dict = Field(default_factory=dict)


# ===========================================================================
# Backend API request/response bodies (SPEC §8)
# ===========================================================================


class CreateSessionRequest(BaseModel):
    """POST /api/agent/sessions body."""

    model_config = _IGNORE_EXTRA

    ticket_id: int


class CreateSessionResponse(BaseModel):
    """Response from POST /api/agent/sessions."""

    session_id: str


class ApproveRequest(BaseModel):
    """POST /api/agent/sessions/{sid}/approve body (SPEC §8)."""

    model_config = _IGNORE_EXTRA

    action_id: str
    edited_command: Optional[str] = None


class RejectRequest(BaseModel):
    """POST /api/agent/sessions/{sid}/reject body (SPEC §8)."""

    model_config = _IGNORE_EXTRA

    action_id: str


class SubmitActivityRequest(BaseModel):
    """POST /api/agent/sessions/{sid}/activity body (SPEC §8)."""

    model_config = _IGNORE_EXTRA

    activity: ActivityCreate
