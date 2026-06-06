"""Tests for the merged-branch adjustments.

Covers: likelihood rendered as a normalised PERCENTAGE, a hypothesis carrying
MULTIPLE check commands, the technician's OWN hypothesis driving the loop, and the
"no hidden cells" guarantee that every executed agent command is echoed to the
terminal stream.
"""
import asyncio

import pytest

from app.agent.loop import _normalize_likelihoods, run_agent
from app.config import Settings
from app.models import Hypothesis, RunPhase, SystemInfo, Ticket, TicketStatus
from app.runs.manager import Run
from app.ssh.runner import CommandResult


class FakeSSH:
    def __init__(self, *args, **kwargs):
        self.connected = False
        self.commands: list[str] = []

    async def connect(self):
        self.connected = True

    async def run_command(self, command, timeout=None):
        self.commands.append(command)
        return CommandResult(command, f"ok: {command}", "", 0)

    async def close(self):
        self.connected = False


class FakeERP:
    def __init__(self):
        self.activities = []
        self.statuses = {}

    async def create_activity(self, activity):
        from app.models import Activity

        self.activities.append(activity)
        return Activity(id=9100, ticket_id=activity.ticket_id,
                        start_datetime=activity.start_datetime,
                        end_datetime=activity.end_datetime, description=activity.description)

    async def set_status(self, ticket_id, status):
        self.statuses[ticket_id] = status


class TwoCheckLLM:
    """Returns one hypothesis with TWO check commands."""

    configured = True
    agent_model = "agent"
    fast_model = "fast"

    async def complete_json(self, system, user, model=None, temperature=0.1, schema=None):
        if "RANKED" in system:
            return {"hypotheses": [{
                "title": "nginx down",
                "reasoning": "failed unit",
                "evidence": "systemctl --failed",
                "proposed_checks": ["systemctl is-active nginx", "journalctl -u nginx -n 5"],
                "likelihood": 0.8,
            }]}
        if "selected one hypothesis" in system or "wrote their own" in system:
            return {"confirmed": True, "reasoning": "inactive", "proposed_fix": {
                "explanation": "start nginx", "commands": ["systemctl enable --now nginx"],
                "service": "nginx", "validation_command": "curl http://localhost/health"}}
        if "verify whether a fix" in system:
            return {"success": True, "validation_result": "HTTP 200 OK"}
        return {"summary": "Restored nginx.", "root_cause": "nginx disabled.",
                "actions_taken": "Enabled nginx.", "commands_summary": "systemctl enable --now nginx",
                "validation_result": "HTTP 200 OK", "description": "Fixed."}


def _make_run(tmp_path, llm_cls=TwoCheckLLM):
    settings = Settings(audit_dir=str(tmp_path))
    run = Run("run-adj", 7001, settings, FakeERP(), auto_approve_reads=True)
    run.ticket = Ticket(id=7001, title="API down", description="502s", priority="high",
                        status="OPEN", customer_id=5001, customer_name="Acme")
    run.system_info = SystemInfo(ip="10.0.0.5", port=22, username="azureuser", os="Ubuntu 22.04")
    return run, settings


# --------------------------------------------------------------------------- #
def test_likelihood_is_normalised_to_percentages():
    hyps = [
        Hypothesis(id="h1", rank=1, title="a", likelihood=0.9),
        Hypothesis(id="h2", rank=2, title="b", likelihood=0.3),
        Hypothesis(id="h3", rank=3, title="c", likelihood=None),  # missing -> still gets a share
    ]
    _normalize_likelihoods(hyps)
    total = sum(h.likelihood for h in hyps)
    assert abs(total - 100.0) < 1.0  # percentages, summing to ~100
    # sorted by likelihood desc, ranks reassigned
    assert [h.rank for h in hyps] == [1, 2, 3]
    assert hyps[0].likelihood >= hyps[1].likelihood >= hyps[2].likelihood
    assert all(0 <= h.likelihood <= 100 for h in hyps)


def test_percentages_handle_values_already_in_percent():
    hyps = [Hypothesis(id="h1", rank=1, title="a", likelihood=70),
            Hypothesis(id="h2", rank=2, title="b", likelihood=30)]
    _normalize_likelihoods(hyps)
    assert abs(sum(h.likelihood for h in hyps) - 100.0) < 1.0
    assert hyps[0].likelihood == pytest.approx(70.0, abs=1.0)


async def _drive_selecting(run, picker):
    for _ in range(500):
        if run.task and run.task.done():
            return
        for approval_id in list(run.pending_approvals):
            run.resolve_approval(approval_id, True, None)
        if run.hypothesis_future and not run.hypothesis_future.done() and run.hypotheses:
            picker(run)
        if run.activity_future and not run.activity_future.done():
            run.submit_activity(None)
        await asyncio.sleep(0.01)


async def test_hypothesis_runs_all_its_checks(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.loop.SSHRunner", FakeSSH)
    run, _ = _make_run(tmp_path)
    run.task = asyncio.create_task(run_agent(run, TwoCheckLLM()))
    await asyncio.wait_for(
        asyncio.gather(_drive_selecting(run, lambda r: r.select_hypothesis(r.hypotheses[0].id)), run.task),
        timeout=15,
    )
    executed = [c.get("command") for c in run.audit.commands()]
    # BOTH check commands of the single hypothesis were run (not just one).
    assert "systemctl is-active nginx" in executed
    assert "journalctl -u nginx -n 5" in executed
    assert run.phase == RunPhase.DONE


async def test_own_hypothesis_drives_the_loop(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.loop.SSHRunner", FakeSSH)
    run, _ = _make_run(tmp_path)

    def submit_own(r):
        r.submit_custom_hypothesis({"title": "my theory", "reasoning": "hunch",
                                    "checks": ["cat /etc/nginx/nginx.conf"]})

    run.task = asyncio.create_task(run_agent(run, TwoCheckLLM()))
    await asyncio.wait_for(asyncio.gather(_drive_selecting(run, submit_own), run.task), timeout=15)
    technician_hyps = [h for h in run.hypotheses if h.source == "technician"]
    assert technician_hyps and technician_hyps[0].title == "my theory"
    executed = [c.get("command") for c in run.audit.commands()]
    assert "cat /etc/nginx/nginx.conf" in executed  # the technician's own check ran


async def test_no_hidden_cells_every_command_is_echoed(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.loop.SSHRunner", FakeSSH)
    run, _ = _make_run(tmp_path)
    run.task = asyncio.create_task(run_agent(run, TwoCheckLLM()))
    await asyncio.wait_for(
        asyncio.gather(_drive_selecting(run, lambda r: r.select_hypothesis(r.hypotheses[0].id)), run.task),
        timeout=15,
    )
    term_text = "".join(e.get("data", "") for e in run.events if e["type"] == "term.data")
    executed = [c["command"] for c in run.audit.commands()
                if not c.get("blocked") and not c.get("rejected")]
    assert executed  # sanity: the agent actually ran commands
    for cmd in executed:
        assert f"$ {cmd}" in term_text, f"command not visible in terminal: {cmd}"


# --------------------------------------------------------------------------- #
# Interactive PTY terminal (adjustment 1) — wiring tests with a fake process.
# --------------------------------------------------------------------------- #
class FakeStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _n):
        return self._chunks.pop(0) if self._chunks else ""


class FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)


class FakeProcess:
    def __init__(self, chunks):
        self.stdout = FakeStdout(chunks)
        self.stdin = FakeStdin()
        self.size = None
        self.closed = False

    def change_terminal_size(self, cols, rows):
        self.size = (cols, rows)

    def close(self):
        self.closed = True


async def test_interactive_shell_pumps_writes_and_resizes():
    from app.ssh.runner import InteractiveShell

    received: list[str] = []
    proc = FakeProcess(["hello\r\n", "$ "])
    shell = InteractiveShell(proc, lambda d: received.append(d))
    await asyncio.sleep(0.02)  # let the reader pump drain stdout
    assert "".join(received) == "hello\r\n$ "
    shell.write("ls\n")
    assert proc.stdin.written == ["ls\n"]
    shell.resize(100, 40)
    assert proc.size == (100, 40)
    shell.close()
    assert proc.closed


class ShellSSH(FakeSSH):
    async def open_shell(self, on_data, cols=120, rows=30):
        from app.ssh.runner import InteractiveShell

        self.shell_size = (cols, rows)
        return InteractiveShell(FakeProcess(["# customer-vm:~ $ "]), on_data)


async def test_run_terminal_write_opens_pty_and_mirrors_output(tmp_path):
    run, _ = _make_run(tmp_path)
    run.ssh = ShellSSH()
    run.ssh.connected = True
    await run.terminal_write("vim /etc/nginx/nginx.conf\n", cols=100, rows=30)
    await asyncio.sleep(0.02)
    term = "".join(e.get("data", "") for e in run.events if e["type"] == "term.data")
    assert "# customer-vm" in term  # live PTY output is mirrored into the terminal
    assert run.ssh.shell_size == (100, 30)  # opened at the client's terminal size
    await run.close_shell()
    assert run.shell is None
