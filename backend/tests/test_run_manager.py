"""Run coordination: approvals, STOP unblocking, hypothesis selection, events."""
import asyncio

import pytest

from app.config import Settings
from app.runs.manager import Run, RunStopped


def make_run(tmp_path, auto: bool = True) -> Run:
    settings = Settings(audit_dir=str(tmp_path))
    return Run("test-run", 7001, settings, erp=None, auto_approve_reads=auto)


async def test_approval_resolves(tmp_path):
    run = make_run(tmp_path)

    async def approve_later():
        await asyncio.sleep(0.01)
        approval_id = next(iter(run.pending_approvals))
        run.resolve_approval(approval_id, True, "ls -la")

    asyncio.create_task(approve_later())
    decision = await run.request_approval("command", {"command": "ls"})
    assert decision.approved is True
    assert decision.edited == "ls -la"


async def test_stop_unblocks_pending_approval(tmp_path):
    run = make_run(tmp_path)

    async def stop_later():
        await asyncio.sleep(0.01)
        run.request_stop()

    asyncio.create_task(stop_later())
    with pytest.raises(RunStopped):
        await run.request_approval("command", {"command": "ls"})


async def test_hypothesis_selection(tmp_path):
    run = make_run(tmp_path)

    async def select_later():
        await asyncio.sleep(0.01)
        run.select_hypothesis("h2")

    asyncio.create_task(select_later())
    selected = await run.await_hypothesis_selection()
    assert selected == "h2"


async def test_event_history_and_live_subscription(tmp_path):
    run = make_run(tmp_path)
    run.info("hello")  # recorded before anyone subscribes
    queue = run.subscribe()
    run.emit("custom", value=1)
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event["type"] == "custom" and event["value"] == 1
    assert any(e["type"] == "info" for e in run.events)  # history retained
