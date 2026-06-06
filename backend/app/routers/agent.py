"""Agent router — SPEC §8.

Manages troubleshooting sessions and the SSE event stream.

Routes
------
POST /api/agent/sessions                       → create session
POST /api/agent/sessions/{sid}/start           → start agent loop
GET  /api/agent/sessions/{sid}/stream          → SSE stream
POST /api/agent/sessions/{sid}/approve         → approve (optionally edit) command
POST /api/agent/sessions/{sid}/reject          → reject command
POST /api/agent/sessions/{sid}/retry           → retry last step
POST /api/agent/sessions/{sid}/abort           → abort session
POST /api/agent/sessions/{sid}/activity        → submit reviewed activity to ERP
POST /api/dev/reset                            → reset ERP state (dev convenience)
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.erp_client import ErpAuthError, ErpError
from app.models import (
    ApproveRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    RejectRequest,
    SubmitActivityRequest,
)
from app.agent.orchestrator import ApprovalDecision
from app.safety.redaction import redact
from app.session import Session, SessionError, SessionManager

logger = logging.getLogger("app.routers.agent")

router = APIRouter()

# ---------------------------------------------------------------------------
# Module-level SessionManager singleton
# Tests can swap it via set_session_manager().
# ---------------------------------------------------------------------------

manager: SessionManager = SessionManager()


def set_session_manager(m: SessionManager) -> None:
    """Replace the module-level manager (used by tests to inject mocks)."""
    global manager
    manager = m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session(session_id: str) -> Session:
    """Return the Session or raise 404."""
    session = manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    return session


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/api/agent/sessions", response_model=CreateSessionResponse)
async def create_session(body: CreateSessionRequest) -> CreateSessionResponse:
    """Create a troubleshooting session for the given ticket.

    Fetches ticket + customer-system from ERP, builds the runner/LLM/orchestrator,
    and returns the new session_id.
    """
    try:
        session = await manager.create(body.ticket_id)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ErpAuthError, ErpError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ERP error: {redact(str(exc))}",
        )
    return CreateSessionResponse(session_id=session.session_id)


@router.post("/api/agent/sessions/{session_id}/start")
async def start_session(session_id: str):
    """Approve connection and begin diagnosis (SPEC §8 connection gate)."""
    session = _get_session(session_id)
    await session.start()
    return {"status": "started"}


@router.get("/api/agent/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    """Server-Sent Events stream for the session (SPEC §9).

    First replays all accumulated events, then streams new ones as they arrive.
    Stops when the session reaches a terminal state.
    """
    session = _get_session(session_id)

    async def _generate() -> AsyncIterator[dict]:
        async for event in session.subscribe():
            yield {
                "event": event.type,
                "data": json.dumps(event.model_dump()),
            }

    return EventSourceResponse(_generate())


@router.post("/api/agent/sessions/{session_id}/approve")
async def approve_command(session_id: str, body: ApproveRequest):
    """Approve (optionally edit) a proposed command."""
    session = _get_session(session_id)
    decision = ApprovalDecision(
        decision="edited" if body.edited_command else "approved",
        edited_command=body.edited_command,
    )
    session.resolve_approval(body.action_id, decision)
    return {"status": "approved", "action_id": body.action_id}


@router.post("/api/agent/sessions/{session_id}/reject")
async def reject_command(session_id: str, body: RejectRequest):
    """Reject a proposed command."""
    session = _get_session(session_id)
    session.resolve_approval(body.action_id, ApprovalDecision("rejected"))
    return {"status": "rejected", "action_id": body.action_id}


@router.post("/api/agent/sessions/{session_id}/retry")
async def retry_session(session_id: str):
    """Retry the last failed/rejected step."""
    session = _get_session(session_id)
    await session.retry()
    return {"status": "retry_initiated"}


@router.post("/api/agent/sessions/{session_id}/abort")
async def abort_session(session_id: str):
    """Abort the session immediately."""
    session = _get_session(session_id)
    session.abort()
    return {"status": "aborted"}


@router.post("/api/agent/sessions/{session_id}/activity")
async def submit_activity(session_id: str, body: SubmitActivityRequest):
    """Submit the human-reviewed activity to ERP and mark the ticket DONE."""
    session = _get_session(session_id)
    try:
        result = await session.submit_activity(body.activity)
    except (ErpAuthError, ErpError) as exc:
        raise HTTPException(
            status_code=502,
            detail=redact(str(exc)),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=redact(str(exc)),
        )

    if result is None:
        return {"status": "DONE", "ticket_id": session.ticket_id}
    return result


@router.post("/api/dev/reset")
async def dev_reset():
    """Reset ERP state (dev convenience — calls /me/reset on the ERP)."""
    try:
        msg = await manager.erp_client.reset()
    except (ErpAuthError, ErpError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ERP error: {redact(str(exc))}",
        )
    return msg
