"""REST API consumed by the technician frontend.

Wraps the Phoenix client for the read flows (tickets, customer system) and starts
/ controls agent runs. The heavy interaction (events, approvals) is over the
WebSocket in app/api/ws.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from app.agent.loop import run_agent, run_shell
from app.erp import PhoenixError
from app.keys import KeyStore
from app.models import (
    ActivityCreate,
    EventType,
    ManualReportRequest,
    RunPhase,
    StartRunRequest,
    StatusUpdate,
    TicketStatus,
    utcnow_iso,
)
from app.runs.resolutions import ResolutionStore

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


@router.post("/me/reset")
async def reset_me(request: Request) -> Any:
    """Dev helper: reset the team's ERP state (clears activities, reboots VMs).

    Surfaced from the frontend dev menu so a test run can start from a clean
    slate. Proxies Phoenix's POST /api/v1/me/reset.
    """
    try:
        return await _erp(request).reset()
    except PhoenixError as exc:
        _raise(exc)


@router.post("/dev/keys")
async def upload_keys(request: Request, files: list[UploadFile] = File(...)) -> Any:
    """Dev helper: upload SSH private keys so runs work with freshly-issued keys.

    Keys land in the backend keys directory (``case{N}_key.pem`` etc.) and, when
    an S3 bucket is configured, are mirrored there as a fallback for other
    replicas. The bytes never leave the backend afterwards.
    """
    settings = request.app.state.settings
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        payloads.append((f.filename or "", await f.read()))
    try:
        saved = KeyStore(settings).save_uploads(payloads)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "saved": [{"name": k.name, "bytes": k.bytes, "s3": k.s3} for k in saved],
        "keys_dir": str(settings.keys_dir),
        "s3": settings.s3_keys_configured,
    }


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


@router.patch("/tickets/{ticket_id}/status")
async def set_ticket_status(request: Request, ticket_id: int, body: StatusUpdate) -> Any:
    """Let the technician override a ticket's ERP status from the overview."""
    try:
        return await _erp(request).set_status(ticket_id, body.status)
    except PhoenixError as exc:
        _raise(exc)


@router.get("/tickets/{ticket_id}/resolution")
async def get_resolution(request: Request, ticket_id: int) -> Any:
    """The finished run that resolved this ticket (solution + full log), or null.

    Lets the ticket page show *how* a DONE ticket was fixed after the run ends —
    the run's submitted activity and complete event log. Null until a run has
    submitted its activity (or once that run is garbage-collected past the cap).
    """
    return _mgr(request).resolution_for_ticket(ticket_id)


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


@router.post("/runs/{run_id}/manual-report")
async def submit_manual_report(
    request: Request, run_id: str, body: ManualReportRequest
) -> Any:
    """Technician files an activity report without waiting for the agent draft."""
    mgr = _mgr(request)
    run = mgr.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.submitted_activity is not None:
        raise HTTPException(status_code=409, detail="this run already has a submitted report")

    fields = body.model_dump(exclude={"outcome"})
    activity = ActivityCreate(
        ticket_id=run.ticket_id,
        start_datetime=run.started_at,
        end_datetime=utcnow_iso(),
        **fields,
    )
    try:
        created = await _erp(request).create_activity(activity)
    except PhoenixError as exc:
        _raise(exc)

    outcome = body.outcome
    final_status = TicketStatus.DONE if outcome == "fixed" else TicketStatus.PENDING
    try:
        await _erp(request).set_status(run.ticket_id, final_status)
    except PhoenixError as exc:
        _raise(exc)

    run.submitted_activity = fields
    run.outcome = outcome
    run.resolved_manually = True
    run.activity_draft = fields
    run.emit(
        EventType.ACTIVITY_SUBMITTED,
        activity_id=getattr(created, "id", None),
        outcome=outcome,
        manual=True,
    )
    run.set_phase(RunPhase.DONE)
    run.info(
        "Manual report submitted and ticket marked "
        f"{'DONE' if final_status == TicketStatus.DONE else 'PENDING'}."
    )

    # Unblock the agent if it was waiting for activity review; then stop it.
    run.submit_activity(fields)
    run.request_stop()

    ResolutionStore(mgr.settings.audit_dir).save(run.ticket_id, run.resolution_record())

    return {
        "activity_id": getattr(created, "id", None),
        "status": final_status.value,
        "outcome": outcome,
    }
