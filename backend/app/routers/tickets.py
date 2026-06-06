"""Tickets router — SPEC §8.

Proxies ERP endpoints through the backend so the frontend never touches the
ERP directly (and so the ERP token never reaches the browser).

Routes
------
GET /api/me
GET /api/tickets?status=&priority=&sort=&customer=
GET /api/tickets/{id}
GET /api/tickets/{id}/customer-system  (redacts system.notes)
"""
from __future__ import annotations

import logging
import typing
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings
from app.erp_client import ErpAuthError, ErpClient, ErpError, make_erp_client
from app.models import CustomerSystem, Employee, Ticket
from app.safety.redaction import redact

logger = logging.getLogger("app.routers.tickets")

router = APIRouter()

# ---------------------------------------------------------------------------
# ERP dependency
# ---------------------------------------------------------------------------


def _get_erp() -> ErpClient:
    """FastAPI dependency that returns the module-level ERP client."""
    return make_erp_client(get_settings())


# ---------------------------------------------------------------------------
# ERP error helpers
# ---------------------------------------------------------------------------


def _handle_erp_error(exc: Exception) -> typing.NoReturn:
    """Map ErpError / ErpAuthError to the correct HTTPException.

    Always raises — annotated NoReturn so type checkers know callers do not
    have an implicit None fall-through path after calling this function.
    """
    if isinstance(exc, ErpAuthError):
        raise HTTPException(
            status_code=401,
            detail="ERP authentication failed — check PHOENIX_API_TOKEN.",
        )
    if isinstance(exc, ErpError):
        raise HTTPException(
            status_code=502,
            detail=f"ERP error: {redact(str(exc))}",
        )
    raise exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/me", response_model=Employee)
async def get_me(erp: ErpClient = Depends(_get_erp)) -> Employee:
    """Return the logged-in technician from the ERP."""
    try:
        return await erp.get_me()
    except (ErpAuthError, ErpError) as exc:
        _handle_erp_error(exc)  # always raises (NoReturn)


@router.get("/api/tickets", response_model=list[Ticket])
async def list_tickets(
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    sort: Optional[str] = Query(default=None),
    customer: Optional[str] = Query(default=None),
    erp: ErpClient = Depends(_get_erp),
) -> list[Ticket]:
    """List tickets assigned to the logged-in technician.

    Supports filtering by status, priority, customer name/id, and sort order.
    Returns an empty list when there are no matching tickets.
    """
    try:
        tickets = await erp.list_tickets(
            status=status,
            priority=priority,
            sort=sort or "date",
        )
    except (ErpAuthError, ErpError) as exc:
        _handle_erp_error(exc)
        return []  # unreachable — _handle_erp_error always raises

    if not tickets:
        return []

    # Filter by customer query string (matches customer_name or customer_id)
    if customer:
        customer_lower = customer.lower()
        tickets = [
            t
            for t in tickets
            if customer_lower in t.customer_name.lower()
            or str(t.customer_id) == customer
        ]

    return tickets


@router.get("/api/tickets/{ticket_id}", response_model=Ticket)
async def get_ticket(
    ticket_id: int,
    erp: ErpClient = Depends(_get_erp),
) -> Ticket:
    """Return a single ticket by ID."""
    try:
        ticket = await erp.get_ticket(ticket_id)
    except (ErpAuthError, ErpError) as exc:
        _handle_erp_error(exc)  # always raises (NoReturn)

    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    return ticket


@router.get("/api/tickets/{ticket_id}/customer-system", response_model=CustomerSystem)
async def get_customer_system(
    ticket_id: int,
    erp: ErpClient = Depends(_get_erp),
) -> CustomerSystem:
    """Return the customer system for a ticket, with notes redacted (SPEC §8/§10)."""
    try:
        cs = await erp.get_customer_system(ticket_id)
    except (ErpAuthError, ErpError) as exc:
        _handle_erp_error(exc)  # always raises (NoReturn)

    if cs is None:
        raise HTTPException(
            status_code=404,
            detail=f"Customer system for ticket {ticket_id} not found",
        )

    # Redact notes before returning to the frontend (SPEC §10 — no secrets in UI)
    safe_system = cs.system.model_copy(update={"notes": redact(cs.system.notes)})
    safe_cs = cs.model_copy(update={"system": safe_system})
    return safe_cs
