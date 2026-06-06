"""REST API consumed by the technician frontend.

Wraps the Phoenix client for the read flows (tickets, customer system) and starts
/ controls agent runs. The heavy interaction (events, approvals) is over the
WebSocket in app/api/ws.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.agent.loop import run_agent
from app.erp import PhoenixError
from app.models import ActivityCreate, StartRunRequest

router = APIRouter(prefix="/api")


def _erp(request: Request) -> Any:
    return request.app.state.erp


def _mgr(request: Request) -> Any:
    return request.app.state.manager


def _llm(request: Request) -> Any:
    return request.app.state.llm


def _raise(exc: PhoenixError) -> None:
    raise HTTPException(status_code=exc.status_code or 502, detail=exc.message)


@router.get("/me")
async def get_me(request: Request) -> Any:
    try:
        return await _erp(request).get_me()
    except PhoenixError as exc:
        _raise(exc)


@router.get("/tickets")
async def list_tickets(
    request: Request,
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    sort: str = Query("date"),
) -> Any:
    try:
        return await _erp(request).list_tickets(status=status, priority=priority, sort=sort)
    except PhoenixError as exc:
        _raise(exc)


@router.get("/tickets/{ticket_id}")
async def get_ticket(request: Request, ticket_id: int) -> Any:
    try:
        return await _erp(request).get_ticket(ticket_id)
    except PhoenixError as exc:
        _raise(exc)


@router.get("/tickets/{ticket_id}/customer-system")
async def get_customer_system(request: Request, ticket_id: int) -> Any:
    try:
        return await _erp(request).get_customer_system(ticket_id)
    except PhoenixError as exc:
        _raise(exc)


@router.post("/runs")
async def start_run(request: Request, body: StartRunRequest) -> Any:
    erp, mgr, llm = _erp(request), _mgr(request), _llm(request)
    try:
        ticket = await erp.get_ticket(body.ticket_id)
        customer_system = await erp.get_customer_system(body.ticket_id)
    except PhoenixError as exc:
        _raise(exc)

    run = mgr.create_run(body.ticket_id, body.auto_approve_reads)
    run.ticket = ticket
    run.system_info = customer_system.system
    run.task = asyncio.create_task(run_agent(run, llm))
    return {"run_id": run.id, "phase": run.phase.value, "auto_approve_reads": run.auto_approve_reads}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Any:
    run = _mgr(request).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run.snapshot()


@router.post("/runs/{run_id}/stop")
async def stop_run(request: Request, run_id: str) -> Any:
    run = _mgr(request).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    run.request_stop()
    return {"ok": True}


@router.post("/runs/{run_id}/activity")
async def submit_activity(request: Request, run_id: str, body: dict) -> Any:
    """Submit (optionally edited) activity fields; resolves the loop's wait."""
    run = _mgr(request).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not run.submit_activity(body):
        raise HTTPException(status_code=409, detail="run is not awaiting an activity submission")
    return {"ok": True}
