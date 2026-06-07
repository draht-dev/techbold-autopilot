"""Autonomous agent orchestration (Cursor / Claude-Code style).

One troubleshooting run is ONE continuous agent conversation. After connecting and
a cheap deterministic recon seed, the agent drives itself with tools: it runs as
many commands as it wants, presents hypotheses early for a blocking human pick,
applies remediation as one reviewable plan via ProposeFix, asks the technician to
decide when blocked, and finishes when the issue is resolved (or closed). Every
command still flows through the gated/audited/redacted ``execute_command`` choke
point, so human approval and secret filtering are intact.
"""
from __future__ import annotations

import shlex
from typing import Any, Optional

from langchain_core.messages import AIMessage

from app.agent import prompts
from app.agent.activity import draft_activity
from app.agent.agent_tools import (
    AGENT_TOOLS,
    FINISH,
    PRESENT_HYPOTHESES,
    PROPOSE_FIX,
    REQUEST_DECISION,
    RUN_COMMAND,
)
from app.agent.llm import LLM, LLMError, reasoning_text
from app.agent.session import AgentSession
from app.agent.tools import RECON_PLAYBOOK, ExecResult, execute_command
from app.models import (
    ActivityCreate,
    EventType,
    Hypothesis,
    RunPhase,
    TicketStatus,
    utcnow_iso,
)
from app.runs.manager import Run, RunStopped
from app.runs.resolutions import ResolutionStore
from app.ssh import SSHError, SSHRunner

# Hard safety cap so a misbehaving model cannot loop forever; the agent is
# otherwise free to make as many tool calls as it needs.
_RESULT_SNIPPET = 1200


async def run_agent(run: Run, llm: LLM) -> None:
    try:
        await _connect(run)
        recon = await _recon(run)
        await _investigate(run, llm, recon)
    except RunStopped:
        run.set_phase(RunPhase.STOPPED)
        run.info("Run stopped by technician.")
        await _safe_set_status(run, TicketStatus.PENDING)
    except SSHError as exc:
        _fail(run, str(exc))
    except LLMError as exc:
        _fail(run, f"LLM error: {exc}")
    except Exception as exc:  # noqa: BLE001 - never crash the event loop
        _fail(run, f"Unexpected error: {exc}")
    finally:
        await run.close_shell()
        if run.ssh:
            await run.ssh.close()


# --------------------------------------------------------------------------- #
def _fail(run: Run, message: str) -> None:
    run.error = message
    run.emit(EventType.ERROR, message=message)
    run.set_phase(RunPhase.ERROR)


def _ticket_context(run: Run) -> str:
    t, si = run.ticket, run.system_info
    return (
        f"TICKET #{t.id}: {t.title}\n"
        f"Priority: {t.priority} | Customer: {t.customer_name}\n"
        f"Customer report (symptom only): {t.description}\n\n"
        f"SYSTEM: {si.os} at {si.ip}:{si.port} (user {si.username})\n"
        f"Notes: {si.notes or '-'}"
    )


async def _safe_set_status(run: Run, status: TicketStatus) -> None:
    try:
        await run.erp.set_status(run.ticket_id, status)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
async def _connect(run: Run) -> None:
    run.set_phase(RunPhase.CONNECTING)
    si = run.system_info
    decision = await run.request_approval(
        "connect",
        {"ip": si.ip, "port": si.port, "username": si.username, "os": si.os},
        "Connect to the customer VM over SSH",
    )
    if not decision.approved:
        run.info("Technician declined the SSH connection.")
        raise RunStopped()
    await _open_ssh(run)


async def _open_ssh(run: Run) -> None:
    """Build the SSH connection for the run (shared by the agent and plain shell)."""
    si = run.system_info
    key_path = run.settings.ssh_key_path_for_ticket(run.ticket_id)
    run.ssh = SSHRunner(
        host=si.ip,
        port=si.port or 22,
        username=si.username or run.settings.ssh_username,
        key_path=key_path,
        connect_timeout=run.settings.ssh_connect_timeout,
        command_timeout=run.settings.ssh_command_timeout,
    )
    key_name = key_path.rsplit("/", 1)[-1]
    run.info(
        f"Connecting to {si.ip}:{si.port} as {si.username or run.settings.ssh_username} "
        f"(key {key_name})..."
    )
    await run.ssh.connect()
    run.info("SSH connection established.")


async def run_shell(run: Run) -> None:
    """A plain interactive SSH session with NO agent loop.

    The technician opens this to inspect a machine directly (e.g. after the ticket
    is resolved): we connect SSH and then idle, keeping the connection alive so the
    interactive PTY (driven over the WebSocket) works. No recon, no LLM, no
    auto-run commands — just a shell. The connection is explicit (the technician
    clicked "open SSH"), so it skips the per-command approval gate's connect prompt.
    """
    try:
        run.set_phase(RunPhase.CONNECTING)
        await _open_ssh(run)
        run.set_phase(RunPhase.SHELL)
        # Open the PTY immediately so the login banner streams to the UI without
        # waiting for the technician's first keystroke (which used to leave a blank
        # terminal until they typed).
        await run.ensure_shell()
        run.info("Plain SSH session ready — the agent is NOT running. Use the terminal below.")
        await run.stop_event.wait()  # idle until the technician closes the session
    except RunStopped:
        pass
    except SSHError as exc:
        _fail(run, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - never crash the event loop
        _fail(run, f"Unexpected error: {exc}")
        return
    finally:
        await run.close_shell()
        if run.ssh:
            await run.ssh.close()
    if run.phase != RunPhase.ERROR:
        run.set_phase(RunPhase.STOPPED)


async def _recon(run: Run) -> str:
    """Cheap, read-only evidence gathering to give the agent a head start."""
    run.set_phase(RunPhase.RECON)
    run.info("Gathering read-only diagnostics...")
    blocks: list[str] = []
    for purpose, cmd in RECON_PLAYBOOK:
        run.check_stop()
        res = await execute_command(run, cmd, actor="agent", purpose=purpose)
        blocks.append(f"### {purpose}\n$ {cmd}\n{res.output}".strip())
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# The autonomous tool loop
# --------------------------------------------------------------------------- #
async def _investigate(run: Run, llm: LLM, recon: str) -> None:
    if not llm.configured:
        run.info(
            "No LLM configured (set OPENROUTER_API_KEY). Recon is complete; "
            "the technician can take over via the terminal."
        )
        return

    settings = run.settings
    session = AgentSession(
        llm,
        prompts.AGENT_SYSTEM,
        model=llm.agent_model,
        max_tokens=settings.agent_context_max_tokens,
        compact_threshold=settings.agent_context_compact_threshold,
        keep_recent_messages=settings.agent_context_keep_recent_messages,
        reasoning_effort=settings.agent_reasoning_effort or None,
    )
    session.add_user(
        _ticket_context(run)
        + "\n\nINITIAL READ-ONLY RECON:\n"
        + recon
        + "\n\nStart by reproducing the reported problem, then proceed."
    )

    run.set_phase(RunPhase.REPRODUCING)
    for _ in range(settings.agent_max_iterations):
        run.check_stop()
        ai = await session.invoke(AGENT_TOOLS)
        _stream_assistant(run, session, ai)

        if not ai.tool_calls:
            # No tool call: nudge the agent to act (or finish) and keep going.
            session.add_user(
                "Continue the investigation. Use a tool (RunCommand / "
                "PresentHypotheses / RequestDecision) or call Finish when done."
            )
            continue

        for call in ai.tool_calls:
            run.check_stop()
            result, done = await _dispatch(run, llm, call)
            session.add_tool_result(call.get("id", ""), result, name=call.get("name"))
            run.stream_agent(kind="tool_result", text=_snippet(result), name=call.get("name"))
            if done:
                return

    run.info("Agent reached the iteration limit without finishing. Technician can take over.")


def _stream_assistant(run: Run, session: AgentSession, ai: AIMessage) -> None:
    text = ai.content if isinstance(ai.content, str) else ""
    if not text and isinstance(ai.content, list):
        text = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in ai.content
        )
    run.stream_agent(
        kind="assistant",
        text=text or "",
        reasoning=reasoning_text(ai),
        tool_calls=[{"name": c.get("name"), "args": c.get("args", {})} for c in ai.tool_calls],
        context_tokens=session.context_tokens,
        compactions=session.compaction_count,
    )


async def _dispatch(run: Run, llm: LLM, call: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool call. Returns ``(tool_result_text, finished)``."""
    name = call.get("name")
    args = call.get("args") or {}

    if name == RUN_COMMAND:
        command = str(args.get("command", "")).strip()
        if not command:
            return "No command provided.", False
        purpose = str(args.get("purpose", "")) or "investigation"
        res = await execute_command(run, command, actor="agent", purpose=purpose)
        return _format_exec(res), False

    if name == PRESENT_HYPOTHESES:
        return await _present_hypotheses(run, args), False

    if name == PROPOSE_FIX:
        return await _propose_fix(run, args), False

    if name == REQUEST_DECISION:
        return await _request_decision(run, args), False

    if name == FINISH:
        await _finalize(run, llm, args)
        return "Run finalized; activity handed to the technician.", True

    return f"Unknown tool '{name}'.", False


def _format_exec(res: ExecResult) -> str:
    status = "ok"
    if res.blocked:
        status = f"BLOCKED ({res.reason})"
    elif res.rejected:
        status = "REJECTED by technician"
    elif res.timed_out:
        status = "timed out"
    header = f"$ {res.command}\n[exit={res.exit_code} status={status}]"
    body = res.output.strip()
    return f"{header}\n{body}".strip()


def _snippet(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _RESULT_SNIPPET else text[:_RESULT_SNIPPET] + "\n[...truncated...]"


# --------------------------------------------------------------------------- #
# Tool: present hypotheses (blocking human pick)
# --------------------------------------------------------------------------- #
async def _present_hypotheses(run: Run, args: dict[str, Any]) -> str:
    raw = args.get("hypotheses") or []
    hyps: list[Hypothesis] = []
    for i, h in enumerate(raw[:5], start=1):
        if not isinstance(h, dict):
            continue
        checks = [str(c).strip() for c in (h.get("proposed_checks") or []) if c and str(c).strip()]
        hyps.append(
            Hypothesis(
                id=f"h{i}",
                rank=i,
                title=str(h.get("title", f"Hypothesis {i}")),
                reasoning=str(h.get("reasoning", "")),
                evidence=str(h.get("evidence", "")),
                checks=checks,
                likelihood=h.get("likelihood"),
                source="agent",
            )
        )
    if not hyps:
        return "No hypotheses were provided; gather more evidence and try again."
    if len(hyps) < 2:
        return (
            f"You provided {len(hyps)} hypothesis. Present at least two distinct candidate "
            "root causes (ranked by likelihood) so the technician has a real choice to "
            "steer - include genuine alternatives even if one seems most likely."
        )

    _normalize_likelihoods(hyps)
    run.hypotheses = hyps
    _emit_hypotheses(run)

    run.set_phase(RunPhase.HYPOTHESES)
    run.info("Waiting for the technician to select a hypothesis...")
    selection = await run.await_hypothesis_selection()
    hyp = _resolve_selection(run, selection)
    if hyp is None:
        return "The technician did not select a usable hypothesis; propose a new list."

    hyp.status = "checking"
    _emit_hypotheses(run)
    run.audit.record("hypothesis_selected", text=hyp.title, source=hyp.source)
    origin = "their own" if hyp.source == "technician" else "a ranked"
    run.info(f"Technician selected {origin} hypothesis: {hyp.title}")
    run.set_phase(RunPhase.INVESTIGATING)

    comment_block = _format_comments(hyp)
    checks = "\n".join(f"- {c}" for c in hyp.checks) or "(none suggested)"
    return (
        f"The technician selected this hypothesis to investigate:\n"
        f"Title: {hyp.title}\n"
        f"Reasoning: {hyp.reasoning}\n"
        f"Suggested read-only checks:\n{checks}{comment_block}\n\n"
        f"Investigate it with RunCommand. Confirm or reject it from the evidence; "
        f"if rejected, gather more evidence and call PresentHypotheses again."
    )


def _normalize_likelihoods(hyps: list[Hypothesis]) -> None:
    """Turn raw model likelihoods into PERCENTAGES across the set, then rank by them.

    The technician should see a relative ``%`` per hypothesis (not a fixed score).
    Missing likelihoods get a rank-decayed share so every hypothesis still gets one.
    """
    if not hyps:
        return
    weights: list[float] = []
    n = len(hyps)
    for i, h in enumerate(hyps):
        v = h.likelihood
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if v is not None and v > 1:  # model already gave a percentage
            v = v / 100.0
        weights.append(v if (v is not None and v > 0) else (n - i) / n * 0.5)
    total = sum(weights) or float(n)
    for h, w in zip(hyps, weights):
        h.likelihood = round(w / total * 100, 1)
    hyps.sort(key=lambda h: h.likelihood or 0.0, reverse=True)
    for i, h in enumerate(hyps, start=1):
        h.rank = i


def _emit_hypotheses(run: Run) -> None:
    run.emit(EventType.HYPOTHESES, items=[h.model_dump() for h in run.hypotheses])


def _format_comments(hyp: Hypothesis) -> str:
    if not hyp.comments:
        return ""
    lines = "\n".join(f"- {c.text}" for c in hyp.comments if c.text)
    return f"\nTECHNICIAN COMMENTS (treat as steering guidance):\n{lines}" if lines else ""


def _resolve_selection(run: Run, selection: Any) -> Optional[Hypothesis]:
    """Map a selection message to a Hypothesis.

    ``{"kind": "existing", "id": ...}`` selects a ranked hypothesis;
    ``{"kind": "custom", "title": ..., "reasoning": ..., "checks": [...]}`` adds the
    technician's OWN hypothesis to the set and selects it.
    """
    if not isinstance(selection, dict):
        return next((h for h in run.hypotheses if h.id == selection), None)
    if selection.get("kind") == "custom":
        checks = [str(c).strip() for c in (selection.get("checks") or []) if c and str(c).strip()]
        new_id = f"t{sum(1 for h in run.hypotheses if h.source == 'technician') + 1}"
        hyp = Hypothesis(
            id=new_id,
            rank=0,
            title=str(selection.get("title") or "Technician hypothesis"),
            reasoning=str(selection.get("reasoning") or ""),
            evidence="Proposed by the technician.",
            checks=checks,
            likelihood=None,
            source="technician",
        )
        run.hypotheses.append(hyp)
        _emit_hypotheses(run)
        return hyp
    return next((h for h in run.hypotheses if h.id == selection.get("id")), None)


# --------------------------------------------------------------------------- #
# Tool: propose + apply a fix as one reviewable plan (human approval)
# --------------------------------------------------------------------------- #
async def _propose_fix(run: Run, args: dict[str, Any]) -> str:
    commands = [str(c).strip() for c in (args.get("commands") or []) if c and str(c).strip()]
    if not commands:
        return "ProposeFix needs at least one command. Describe the fix as a concrete command list."

    explanation = str(args.get("explanation", ""))
    validation_command = (args.get("validation_command") or "") or None
    service = (args.get("service") or "") or None
    rollback = str(args.get("rollback", ""))

    run.set_phase(RunPhase.FIX_PROPOSE)
    decision = await run.request_approval(
        "fix",
        {
            "explanation": explanation,
            "commands": commands,
            "service": service,
            "validation_command": validation_command,
            "rollback": rollback,
        },
        "Apply the proposed fix",
    )
    if not decision.approved:
        run.info("Fix rejected by technician.")
        run.set_phase(RunPhase.INVESTIGATING)
        return (
            "The technician rejected the proposed fix. Reconsider the root cause or "
            "propose a different, more minimal plan."
        )
    if decision.edited:
        commands = [c.strip() for c in decision.edited.splitlines() if c.strip()]

    run.audit.record("fix_approved", text=explanation, commands=commands)
    results: list[str] = []

    run.set_phase(RunPhase.APPLY)
    for cmd in commands:
        res = await execute_command(run, cmd, actor="agent", purpose="apply fix", require_confirm=False)
        results.append(_format_exec(res))
        if res.blocked:
            run.set_phase(RunPhase.INVESTIGATING)
            return (
                "A fix command was blocked by the safety layer; the fix was not fully "
                "applied:\n\n" + "\n\n".join(results)
            )

    if validation_command:
        run.set_phase(RunPhase.VALIDATE)
        res = await execute_command(
            run, validation_command, actor="agent", purpose="validate fix", require_confirm=False
        )
        results.append("VALIDATION:\n" + _format_exec(res))

    if service:
        run.set_phase(RunPhase.PERSIST_VERIFY)
        restart = await execute_command(
            run, f"systemctl restart {shlex.quote(str(service))}",
            actor="agent", purpose="persistence restart", require_confirm=False,
        )
        results.append("PERSISTENCE RESTART:\n" + _format_exec(restart))
        if validation_command:
            reval = await execute_command(
                run, validation_command, actor="agent",
                purpose="re-validate after restart", require_confirm=False,
            )
            results.append("RE-VALIDATION AFTER RESTART:\n" + _format_exec(reval))

    run.set_phase(RunPhase.INVESTIGATING)
    return (
        "The fix plan was approved and applied. Results:\n\n"
        + "\n\n".join(results)
        + "\n\nReview the validation output. If the customer benefit is genuinely "
        "restored, call Finish with outcome 'fixed'; otherwise keep investigating."
    )


# --------------------------------------------------------------------------- #
# Tool: request a technician decision (blocking)
# --------------------------------------------------------------------------- #
async def _request_decision(run: Run, args: dict[str, Any]) -> str:
    question = str(args.get("question", "")).strip() or "How should I proceed?"
    options = [str(o).strip() for o in (args.get("options") or []) if str(o).strip()]
    if not options:
        options = ["Continue investigating", "Stop"]
    context = str(args.get("context", ""))

    prev_phase = run.phase
    run.set_phase(RunPhase.AWAITING_INPUT)
    run.info(f"Waiting for a technician decision: {question}")
    choice = await run.await_decision(question, options, context)
    run.info(f"Technician decision: {choice}")
    if prev_phase not in (RunPhase.CONNECTING, RunPhase.RECON):
        run.set_phase(prev_phase)
    else:
        run.set_phase(RunPhase.INVESTIGATING)
    return (
        f"The technician chose: {choice}\n"
        f"Act on this decision. If they chose to close the ticket, call Finish "
        f"with the appropriate outcome."
    )


# --------------------------------------------------------------------------- #
# Tool: finish (draft activity -> human review -> submit)
# --------------------------------------------------------------------------- #
async def _finalize(run: Run, llm: LLM, args: dict[str, Any]) -> None:
    outcome = str(args.get("outcome", "fixed"))
    note = str(args.get("note", "")).strip()
    if note:
        run.audit.record("note", text=f"Agent closing note ({outcome}): {note}")

    run.set_phase(RunPhase.ACTIVITY_DRAFT)
    run.info(f"Drafting activity documentation (outcome: {outcome})...")
    draft = await draft_activity(run, llm)
    run.activity_draft = draft
    run.emit(EventType.ACTIVITY_DRAFT, draft=draft, outcome=outcome)

    edited = await run.await_activity_submission()
    fields = edited or draft

    run.set_phase(RunPhase.SUBMITTING)
    activity = ActivityCreate(
        ticket_id=run.ticket_id,
        start_datetime=run.started_at,
        end_datetime=utcnow_iso(),
        description=str(fields.get("description", "")),
        summary=str(fields.get("summary", "")),
        root_cause=str(fields.get("root_cause", "")),
        actions_taken=str(fields.get("actions_taken", "")),
        commands_summary=str(fields.get("commands_summary", "")),
        validation_result=str(fields.get("validation_result", "")),
    )
    try:
        created = await run.erp.create_activity(activity)
    except Exception as exc:  # noqa: BLE001
        _fail(run, f"Failed to submit activity: {exc}")
        return

    # Record the solution on the run so the ticket page can show how it was fixed.
    run.submitted_activity = dict(fields)
    run.outcome = outcome
    run.audit.record("activity_submitted", activity_id=getattr(created, "id", None), outcome=outcome)
    run.emit(EventType.ACTIVITY_SUBMITTED, activity_id=getattr(created, "id", None), outcome=outcome)
    # A validated fix marks the ticket DONE; anything else returns it to the queue.
    final_status = TicketStatus.DONE if outcome == "fixed" else TicketStatus.PENDING
    await _safe_set_status(run, final_status)
    run.set_phase(RunPhase.DONE)
    run.info(
        "Activity submitted and ticket marked "
        f"{'DONE' if final_status == TicketStatus.DONE else 'PENDING'}."
    )
    # Durably persist the solution (activity), keyed by ticket, so it survives this
    # run being GC'd and the backend restarting. Phoenix accepts the activity but
    # offers no read-back, so this on-disk copy is the only way the ticket page can
    # retrieve the resolution later. The full event log is NOT persisted (kept in
    # memory only) — the activity is the durable record.
    ResolutionStore(run.settings.audit_dir).save(run.ticket_id, run.resolution_record())
