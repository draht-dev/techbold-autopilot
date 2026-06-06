"""WebSocket endpoint: streams run events and receives technician decisions.

On connect the full event history is replayed (so a late joiner sees the whole
run), then new events stream live. Inbound messages drive the human-in-the-loop
controls: hypothesis selection, approvals, mode toggle, manual terminal, STOP.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.tools import execute_command

router = APIRouter()


@router.websocket("/ws/runs/{run_id}")
async def run_ws(websocket: WebSocket, run_id: str) -> None:
    manager = websocket.app.state.manager
    await websocket.accept()
    run = manager.get(run_id)
    if run is None:
        await websocket.send_json({"type": "error", "message": "run not found"})
        await websocket.close()
        return

    queue = run.subscribe()
    for event in list(run.events):  # replay history
        await websocket.send_json(event)

    sender = asyncio.create_task(_sender(websocket, queue))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle(run, msg)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        run.unsubscribe(queue)


async def _sender(websocket: WebSocket, queue: asyncio.Queue) -> None:
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _handle(run: Any, msg: dict[str, Any]) -> None:
    msg_type = msg.get("type")
    if msg_type == "select_hypothesis":
        run.select_hypothesis(msg.get("id"))
    elif msg_type == "submit_hypothesis":
        # Technician proposes their OWN hypothesis instead of picking a ranked one.
        run.submit_custom_hypothesis(msg.get("hypothesis") or msg)
    elif msg_type == "comment_hypothesis":
        run.comment_hypothesis(msg.get("id"), msg.get("text"))
    elif msg_type == "approval.decision":
        run.resolve_approval(msg.get("id"), bool(msg.get("approved")), msg.get("edited"))
    elif msg_type == "decision":
        # Technician answers an agent RequestDecision (e.g. how to proceed when the
        # reported bug cannot be reproduced).
        run.resolve_decision(msg.get("choice"))
    elif msg_type == "mode.set":
        run.auto_approve_reads = bool(msg.get("auto_approve_reads"))
        run.info(f"Auto-approve safe reads: {run.auto_approve_reads}")
    elif msg_type == "submit_activity":
        run.submit_activity(msg.get("activity"))
    elif msg_type == "stop":
        run.request_stop()
    elif msg_type == "terminal.data":
        # Raw keystrokes from the interactive xterm -> the VM's PTY (vim/htop/etc.).
        asyncio.create_task(
            run.terminal_write(msg.get("data") or "", msg.get("cols"), msg.get("rows"))
        )
    elif msg_type == "terminal.resize":
        asyncio.create_task(
            run.terminal_resize(int(msg.get("cols") or 120), int(msg.get("rows") or 30))
        )
    elif msg_type == "terminal.input":
        # Legacy one-shot command box: still routed through the gated/audited path.
        command = (msg.get("command") or "").strip()
        if command:
            asyncio.create_task(
                execute_command(run, command, actor="human", purpose="manual terminal")
            )
