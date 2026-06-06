"""End-to-end agent loop with fakes (the walking skeleton).

Exercises connect -> recon -> hypotheses -> check -> fix -> validate -> persist ->
activity, driving every human gate (approvals, selection, submission) and asserting
the run reaches DONE and writes the activity + sets the ticket DONE.
"""
import asyncio

from app.agent.loop import run_agent
from app.config import Settings
from app.models import Activity, RunPhase, SystemInfo, Ticket, TicketStatus
from app.runs.manager import Run
from app.ssh.runner import CommandResult


class FakeSSH:
    def __init__(self, *args, **kwargs):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def run_command(self, command, timeout=None):
        return CommandResult(command, f"ok: {command}", "", 0)

    async def close(self):
        self.connected = False


class FakeERP:
    def __init__(self):
        self.activities = []
        self.statuses = {}

    async def create_activity(self, activity):
        self.activities.append(activity)
        return Activity(
            id=9001,
            ticket_id=activity.ticket_id,
            start_datetime=activity.start_datetime,
            end_datetime=activity.end_datetime,
            description=activity.description,
        )

    async def set_status(self, ticket_id, status):
        self.statuses[ticket_id] = status


class FakeLLM:
    configured = True
    agent_model = "agent"
    fast_model = "fast"

    async def complete_json(self, system, user, model=None, temperature=0.1, schema=None):
        if "RANKED" in system:
            return {
                "hypotheses": [
                    {
                        "title": "nginx service is down",
                        "reasoning": "failed unit in recon",
                        "evidence": "systemctl --failed",
                        "proposed_check": "systemctl is-active nginx",
                        "likelihood": 0.9,
                    }
                ]
            }
        if "selected one hypothesis" in system:
            return {
                "confirmed": True,
                "reasoning": "inactive",
                "proposed_fix": {
                    "explanation": "enable and start nginx",
                    "commands": ["systemctl enable --now nginx"],
                    "service": "nginx",
                    "validation_command": "curl http://localhost/health",
                },
            }
        if "verify whether a fix" in system:
            return {"success": True, "validation_result": "HTTP 200 OK"}
        return {
            "summary": "Restored nginx.",
            "root_cause": "nginx unit was disabled.",
            "actions_taken": "Enabled and started nginx.",
            "commands_summary": "systemctl enable --now nginx",
            "validation_result": "HTTP 200 OK",
            "description": "Fixed and validated.",
        }

    async def complete_text(self, *args, **kwargs):
        return ""


async def _drive(run: Run) -> None:
    """Approve everything, pick the first hypothesis, accept the activity draft."""
    for _ in range(500):
        if run.task and run.task.done():
            return
        for approval_id in list(run.pending_approvals):
            run.resolve_approval(approval_id, True, None)
        if run.hypothesis_future and not run.hypothesis_future.done():
            if run.hypotheses:
                run.select_hypothesis(run.hypotheses[0].id)
        if run.activity_future and not run.activity_future.done():
            run.submit_activity(None)
        await asyncio.sleep(0.01)


async def test_full_run_reaches_done(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.loop.SSHRunner", FakeSSH)
    settings = Settings(audit_dir=str(tmp_path))
    erp = FakeERP()
    run = Run("run-e2e", 7001, settings, erp, auto_approve_reads=True)
    run.ticket = Ticket(
        id=7001, title="Status API down", description="502s", priority="high",
        status="OPEN", customer_id=5001, customer_name="Acme",
    )
    run.system_info = SystemInfo(ip="10.0.0.5", port=22, username="azureuser", os="Ubuntu 22.04")

    run.task = asyncio.create_task(run_agent(run, FakeLLM()))
    await asyncio.wait_for(asyncio.gather(_drive(run), run.task), timeout=15)

    assert run.phase == RunPhase.DONE
    assert len(erp.activities) == 1
    assert erp.activities[0].root_cause == "nginx unit was disabled."
    assert erp.statuses.get(7001) == TicketStatus.DONE
    # The audit log captured commands (recon + check + fix + validation).
    assert len(run.audit.commands()) >= 8
