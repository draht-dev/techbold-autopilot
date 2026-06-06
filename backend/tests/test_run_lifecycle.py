"""Run lifecycle: SSH-session ownership, supersede-on-restart, GC, replay bounds.

These cover the four reload/restart fragilities:
  1. restarting a ticket supersedes (tears down) the old run — no orphaned SSH
  2. an in-flight run is discoverable per ticket and globally (resume, not lost)
  3. terminal scrollback retained for replay is bounded (no unbounded growth)
  (4. is a frontend-only change: apply the snapshot phase on reconnect.)
"""
import asyncio

import pytest

from app.config import Settings
from app.models import RunPhase
from app.runs.manager import Run, RunManager


def _settings(tmp_path, **over):
    return Settings(audit_dir=str(tmp_path), **over)


class RecordingSSH:
    """Stand-in for SSHRunner that records whether close() was called."""

    def __init__(self):
        self.connected = True
        self.closed = False

    async def close(self):
        self.closed = True
        self.connected = False


# --------------------------------------------------------------------------- #
# #3 — bounded terminal scrollback, semantic events kept whole
# --------------------------------------------------------------------------- #
def test_term_data_kept_out_of_semantic_events(tmp_path):
    run = Run("r", 7001, _settings(tmp_path), erp=None, auto_approve_reads=True)
    run.emit("term.data", data="hello")
    run.info("an info line")

    assert all(e["type"] != "term.data" for e in run.events)  # not in semantic log
    assert run.terminal_text() == "hello"  # but captured for the terminal pane
    # ...and a late joiner still replays it
    assert any(e["type"] == "term.data" for e in run.replay_events())
    assert any(e["type"] == "info" for e in run.replay_events())


def test_term_buffer_is_bounded(tmp_path):
    run = Run("r", 7001, _settings(tmp_path, term_replay_max_chars=1000),
              erp=None, auto_approve_reads=True)
    for _ in range(500):  # 500 * 100 = 50_000 chars emitted
        run.emit("term.data", data="x" * 100)

    text = run.terminal_text()
    assert text == "x" * 1000  # only the most recent ~cap chars retained
    # semantic events are never evicted by terminal pressure
    run.info("still here")
    assert any(e["type"] == "info" for e in run.events)


# --------------------------------------------------------------------------- #
# #1 — restarting a ticket supersedes the in-flight run (no SSH leak)
# --------------------------------------------------------------------------- #
async def test_create_run_supersedes_active_run_for_ticket(tmp_path):
    mgr = RunManager(_settings(tmp_path))
    run_a = await mgr.create_run(7001, True)
    run_a.ssh = RecordingSSH()

    async def _block_forever():
        await asyncio.Event().wait()

    run_a.task = asyncio.create_task(_block_forever())
    await asyncio.sleep(0)  # let the task start

    run_b = await mgr.create_run(7001, True)  # restart same ticket

    assert run_b.id != run_a.id
    assert run_a.task.done()  # old agent task was cancelled
    assert run_a.ssh.closed is True  # old SSH connection released
    assert run_a.phase == RunPhase.STOPPED
    assert mgr.active_run_for_ticket(7001).id == run_b.id  # pointer now the new run


async def test_terminate_run_is_idempotent_and_closes_ssh(tmp_path):
    mgr = RunManager(_settings(tmp_path))
    run = await mgr.create_run(7001, True)
    run.ssh = RecordingSSH()

    await mgr.terminate_run(run)
    assert run.ssh.closed is True
    assert mgr.active_run_for_ticket(7001) is None
    await mgr.terminate_run(run)  # second call must not raise


async def test_terminate_run_broadcasts_and_audits_stopped(tmp_path):
    """A superseded run's STOPPED transition reaches live subscribers + the audit."""
    mgr = RunManager(_settings(tmp_path))
    run = await mgr.create_run(7001, True)
    q = run.subscribe()

    await mgr.terminate_run(run)

    assert run.phase == RunPhase.STOPPED
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    assert any(e["type"] == "run.state" and e["phase"] == "STOPPED" for e in drained)
    assert any(
        r.get("type") == "phase" and r.get("detail") == "STOPPED" for r in run.audit.entries
    )


async def test_concurrent_create_run_keeps_one_active_with_ssh(tmp_path):
    """Two simultaneous starts for the same ticket must not orphan a live run/SSH."""
    mgr = RunManager(_settings(tmp_path))

    def make_start():
        def start(run):
            run.ssh = RecordingSSH()

            async def _block():
                await asyncio.Event().wait()

            run.task = asyncio.create_task(_block())

        return start

    runs = await asyncio.gather(
        mgr.create_run(7001, True, start=make_start()),
        mgr.create_run(7001, True, start=make_start()),
    )
    await asyncio.sleep(0.01)

    active = mgr.active_run_for_ticket(7001)
    assert active is not None
    live = [r for r in runs if r.ssh and not r.ssh.closed and not r.task.done()]
    superseded = [r for r in runs if r.id != active.id]
    assert len(live) == 1 and live[0].id == active.id  # exactly one survivor
    assert len(superseded) == 1
    assert superseded[0].ssh.closed is True  # the other's SSH was released
    assert superseded[0].task.done()
    await mgr.shutdown()


async def test_shutdown_terminates_all_runs(tmp_path):
    mgr = RunManager(_settings(tmp_path))
    a = await mgr.create_run(7001, True)
    b = await mgr.create_run(7002, True)
    a.ssh, b.ssh = RecordingSSH(), RecordingSSH()

    await mgr.shutdown()
    assert a.ssh.closed and b.ssh.closed


# --------------------------------------------------------------------------- #
# #2 — in-flight runs are discoverable (resume, not lost on reload)
# --------------------------------------------------------------------------- #
async def test_active_run_cleared_when_final(tmp_path):
    mgr = RunManager(_settings(tmp_path))
    run = await mgr.create_run(7002, True)
    assert mgr.active_run_for_ticket(7002).id == run.id

    run.phase = RunPhase.DONE
    assert mgr.active_run_for_ticket(7002) is None
    assert 7002 not in mgr.active_by_ticket


async def test_list_runs_reports_active_flag(tmp_path):
    mgr = RunManager(_settings(tmp_path))
    a = await mgr.create_run(7001, True)
    b = await mgr.create_run(7002, True)
    b.phase = RunPhase.DONE

    by_id = {r["id"]: r for r in mgr.list_runs()}
    assert by_id[a.id]["active"] is True
    assert by_id[b.id]["active"] is False
    assert by_id[a.id]["ticket_id"] == 7001


# --------------------------------------------------------------------------- #
# #1 (memory) — finished runs are garbage-collected past the cap
# --------------------------------------------------------------------------- #
async def test_gc_evicts_oldest_finished_runs(tmp_path):
    mgr = RunManager(_settings(tmp_path, max_retained_runs=3))
    ids = []
    for i in range(6):  # distinct tickets so none supersede each other
        run = await mgr.create_run(8000 + i, True)
        run.phase = RunPhase.DONE  # finished -> eligible for GC on the next create
        ids.append(run.id)
        await asyncio.sleep(0.001)  # distinct started_at for deterministic ordering

    assert len(mgr.runs) <= 3
    assert ids[-1] in mgr.runs  # newest survives
    assert ids[0] not in mgr.runs  # oldest evicted


async def test_gc_never_evicts_active_runs(tmp_path):
    mgr = RunManager(_settings(tmp_path, max_retained_runs=2))
    ids = []
    for i in range(5):  # all left active (no final phase)
        run = await mgr.create_run(8100 + i, True)
        ids.append(run.id)
        await asyncio.sleep(0.001)

    # nothing is finished, so nothing can be evicted even over the cap
    assert all(i in mgr.runs for i in ids)


# --------------------------------------------------------------------------- #
# Resolution lookup — the finished run that documents how a ticket was fixed
# (so a DONE ticket can show its solution + full log after the run ends).
# --------------------------------------------------------------------------- #
async def test_resolved_run_for_ticket_returns_newest_with_activity(tmp_path):
    mgr = RunManager(_settings(tmp_path))
    a = await mgr.create_run(7001, True)
    a.submitted_activity = {"summary": "Restarted the unit"}
    a.outcome = "fixed"
    a.phase = RunPhase.DONE
    await asyncio.sleep(0.001)

    # A later plain-SSH (shell) run for the same ticket has no submitted activity,
    # so it must NOT shadow the resolving agent run.
    b = await mgr.create_run(7001, True)
    b.kind = "shell"

    resolved = mgr.resolved_run_for_ticket(7001)
    assert resolved is not None and resolved.id == a.id
    assert mgr.resolved_run_for_ticket(9999) is None


def test_snapshot_includes_resolution_fields(tmp_path):
    run = Run("r", 7001, _settings(tmp_path), erp=None, auto_approve_reads=True)
    # Defaults: an agent run with no resolution yet.
    snap = run.snapshot()
    assert snap["kind"] == "agent"
    assert snap["submitted_activity"] is None
    assert snap["outcome"] is None

    run.submitted_activity = {"summary": "Fixed it"}
    run.outcome = "fixed"
    assert run.snapshot()["submitted_activity"]["summary"] == "Fixed it"
    assert run.snapshot()["outcome"] == "fixed"
