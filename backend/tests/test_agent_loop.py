"""End-to-end autonomous agent loop with fakes (the walking skeleton).

The agent is one continuous tool-calling conversation. A scripted ``FakeLLM``
returns tool calls (RunCommand -> PresentHypotheses -> RunCommand -> ProposeFix ->
Finish) and we drive every human gate (connect/command approvals, hypothesis
selection, fix approval, decision, activity submission), asserting the run reaches
DONE, writes the activity, and that the agent's stream is emitted.
"""
import asyncio

from langchain_core.messages import AIMessage

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


class ScriptLLM:
    """Returns a fixed sequence of tool calls, one assistant turn per ``invoke``."""

    configured = True
    agent_model = "agent"
    fast_model = "fast"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def complete_with_tools(self, messages, tools, model=None, reasoning_effort=None):
        idx = self.calls
        self.calls += 1
        step = (
            self.script[idx]
            if idx < len(self.script)
            else [("Finish", {"outcome": "fixed", "note": "done"})]
        )
        tool_calls = [
            {"name": name, "args": args, "id": f"call{idx}_{i}"}
            for i, (name, args) in enumerate(step)
        ]
        return AIMessage(content="(thinking)", tool_calls=tool_calls)

    async def complete_json(self, system, user, model=None, temperature=0.1, schema=None):
        return {
            "summary": "Restored nginx.",
            "root_cause": "nginx unit was disabled.",
            "actions_taken": "Enabled and started nginx.",
            "commands_summary": "systemctl enable --now nginx",
            "validation_result": "HTTP 200 OK",
            "description": "Fixed and validated.",
        }

    async def complete_text(self, *args, **kwargs):
        return "Earlier: recon and reproduction."


def _make_run(tmp_path):
    settings = Settings(audit_dir=str(tmp_path))
    run = Run("run-e2e", 7001, settings, FakeERP(), auto_approve_reads=True)
    run.ticket = Ticket(
        id=7001, title="Status API down", description="502s", priority="high",
        status="OPEN", customer_id=5001, customer_name="Acme",
    )
    run.system_info = SystemInfo(ip="10.0.0.5", port=22, username="azureuser", os="Ubuntu 22.04")
    return run


async def _drive(run: Run, *, decision_choice=None) -> None:
    """Approve everything, pick the first hypothesis, answer decisions, accept draft."""
    for _ in range(2000):
        if run.task and run.task.done():
            return
        for approval_id in list(run.pending_approvals):
            run.resolve_approval(approval_id, True, None)
        if run.hypothesis_future and not run.hypothesis_future.done() and run.hypotheses:
            run.select_hypothesis(run.hypotheses[0].id)
        if run.decision_future and not run.decision_future.done():
            run.resolve_decision(decision_choice or "Continue investigating")
        if run.activity_future and not run.activity_future.done():
            run.submit_activity(None)
        await asyncio.sleep(0.005)


_HAPPY_PATH = [
    [("RunCommand", {"command": "curl -fsS http://localhost/health", "purpose": "reproduce"})],
    [(
        "PresentHypotheses",
        {"hypotheses": [
            {
                "title": "nginx service is down",
                "reasoning": "failed unit in recon",
                "evidence": "systemctl --failed",
                "proposed_checks": ["systemctl is-active nginx"],
                "likelihood": 0.9,
            },
            {
                "title": "upstream app not listening",
                "reasoning": "502 could be a dead upstream",
                "evidence": "ss -ltnp",
                "proposed_checks": ["ss -ltnp"],
                "likelihood": 0.3,
            },
        ]},
    )],
    [("RunCommand", {"command": "systemctl is-active nginx", "purpose": "confirm hypothesis"})],
    [(
        "ProposeFix",
        {
            "explanation": "Re-enable and start the nginx unit so it survives reboot.",
            "commands": ["systemctl enable --now nginx"],
            "service": "nginx",
            "validation_command": "curl -fsS http://localhost/health",
            "rollback": "systemctl disable --now nginx",
        },
    )],
    [("Finish", {"outcome": "fixed", "note": "nginx restored and validated"})],
]


async def test_full_run_reaches_done(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.loop.SSHRunner", FakeSSH)
    run = _make_run(tmp_path)
    erp = run.erp

    run.task = asyncio.create_task(run_agent(run, ScriptLLM(_HAPPY_PATH)))
    await asyncio.wait_for(asyncio.gather(_drive(run), run.task), timeout=15)

    assert run.phase == RunPhase.DONE
    assert len(erp.activities) == 1
    assert erp.activities[0].root_cause == "nginx unit was disabled."
    assert erp.statuses.get(7001) == TicketStatus.DONE
    # recon seed + reproduce + fix + validate commands were audited.
    assert len(run.audit.commands()) >= 8
    # The fix was applied through the ProposeFix checkpoint.
    assert "systemctl enable --now nginx" in [c.get("command") for c in run.audit.commands()]
    # The continuous agent streamed its thinking / tool flow to the dev window.
    assert any(e["type"] == "agent.message" for e in run.events)


_NOT_REPRODUCIBLE = [
    [("RunCommand", {"command": "curl -fsS http://localhost/health", "purpose": "reproduce"})],
    [(
        "RequestDecision",
        {
            "question": "I cannot reproduce the reported outage. How should I proceed?",
            "options": ["Investigate anyway", "Close as not reproducible", "Stop"],
        },
    )],
    [("Finish", {"outcome": "not_reproducible", "note": "Service healthy; cannot reproduce."})],
]


async def test_not_reproducible_pauses_for_decision_then_closes_pending(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.loop.SSHRunner", FakeSSH)
    run = _make_run(tmp_path)
    erp = run.erp

    run.task = asyncio.create_task(run_agent(run, ScriptLLM(_NOT_REPRODUCIBLE)))
    await asyncio.wait_for(
        asyncio.gather(_drive(run, decision_choice="Close as not reproducible"), run.task),
        timeout=15,
    )

    assert run.phase == RunPhase.DONE  # finished cleanly even without a fix
    assert len(erp.activities) == 1
    # Not reproducible -> ticket returns to the queue, not DONE.
    assert erp.statuses.get(7001) == TicketStatus.PENDING
    assert any(e["type"] == "decision.request" for e in run.events)
    assert any(e["type"] == "decision.resolved" for e in run.events)
