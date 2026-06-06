"""REST API consumed by the technician frontend.

Wraps the Phoenix client for the read flows (tickets, customer system) and starts
/ controls agent runs. The heavy interaction (events, approvals) is over the
WebSocket in app/api/ws.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.agent.loop import run_agent, run_shell
from app.erp import PhoenixError
from app.models import ActivityCreate, StartRunRequest, TicketStatus

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


@router.get("/tickets/{ticket_id}/active-run")
async def get_active_run(request: Request, ticket_id: int) -> Any:
    """The in-flight run for this ticket (so the UI can resume), or null.

    Returns a lightweight summary (not the full event log) — the resume UI only
    needs id + phase.
    """
    mgr = _mgr(request)
    run = mgr.active_run_for_ticket(ticket_id)
    return mgr.summarize(run) if run is not None else None


@router.get("/tickets/{ticket_id}/resolution")
async def get_resolution(request: Request, ticket_id: int) -> Any:
    """The finished run that resolved this ticket (solution + full log), or null.

    Lets the ticket page show *how* a DONE ticket was fixed after the run ends —
    the run's submitted activity and complete event log. Null until a run has
    submitted its activity (or once that run is garbage-collected past the cap).
    """
    mgr = _mgr(request)
    run = mgr.resolved_run_for_ticket(ticket_id)
    return run.snapshot() if run is not None else None


@router.post("/runs")
async def start_run(request: Request, body: StartRunRequest) -> Any:
    erp, mgr, llm = _erp(request), _mgr(request), _llm(request)
    try:
        ticket = await erp.get_ticket(body.ticket_id)
        customer_system = await erp.get_customer_system(body.ticket_id)
    except PhoenixError as exc:
        _raise(exc)

    # A resolved ticket is closed: re-running the autonomous agent on it is not
    # allowed (use POST /runs/shell for plain, agent-free SSH access instead).
    if ticket.status == TicketStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail="Ticket is DONE — the AI agent cannot be re-run. Open a plain SSH session instead.",
        )

    def start(run: Any) -> None:
        # Runs synchronously inside create_run's per-ticket lock, so registering the
        # run and spawning its agent task is atomic (no concurrent-start orphans).
        run.ticket = ticket
        run.system_info = customer_system.system
        run.task = asyncio.create_task(run_agent(run, llm))

    # Supersedes any in-flight run for this ticket (no orphaned SSH sessions).
    run = await mgr.create_run(body.ticket_id, body.auto_approve_reads, start=start)
    return {"run_id": run.id, "phase": run.phase.value, "auto_approve_reads": run.auto_approve_reads}


@router.post("/runs/shell")
async def start_shell_run(request: Request, body: StartRunRequest) -> Any:
    """Open a plain interactive SSH session — no agent loop.

    Available regardless of ticket status (including DONE), so a technician can
    inspect the machine directly without re-running the autonomous agent.
    """
    erp, mgr = _erp(request), _mgr(request)
    try:
        customer_system = await erp.get_customer_system(body.ticket_id)
    except PhoenixError as exc:
        _raise(exc)

    def start(run: Any) -> None:
        run.kind = "shell"
        run.system_info = customer_system.system
        run.task = asyncio.create_task(run_shell(run))

    run = await mgr.create_run(body.ticket_id, auto_approve_reads=False, start=start)
    return {"run_id": run.id, "phase": run.phase.value, "kind": run.kind}


@router.get("/runs")
async def list_runs(request: Request) -> Any:
    """All runs (newest first) so the UI can surface/resume in-flight ones."""
    return _mgr(request).list_runs()


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
