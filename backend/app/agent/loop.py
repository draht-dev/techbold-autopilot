"""Agent orchestration loop (Cursor Debug Mode style).

Phases: connect -> recon -> ranked hypotheses (human picks) -> check -> on confirm,
propose minimal fix (human approves) -> apply -> validate -> verify persistence ->
draft activity (human reviews) -> submit + mark DONE. STOP unblocks at any point.
"""
from __future__ import annotations

import shlex
from typing import Any, Optional

from app.agent import prompts
from app.agent.activity import draft_activity
from app.agent.llm import LLM, LLMError
from app.agent.schemas import CheckOutput, HypothesesOutput, ValidationOutput
from app.agent.tools import RECON_PLAYBOOK, execute_command
from app.models import (
    ActivityCreate,
    EventType,
    Hypothesis,
    RunPhase,
    TicketStatus,
    utcnow_iso,
)
from app.runs.manager import Run, RunStopped
from app.ssh import SSHError, SSHRunner

MAX_HYPOTHESIS_ROUNDS = 6


async def run_agent(run: Run, llm: LLM) -> None:
    try:
        await _connect(run)
        recon = await _recon(run)
        await _diagnose(run, llm, recon)
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


async def _recon(run: Run) -> str:
    run.set_phase(RunPhase.RECON)
    run.info("Gathering read-only diagnostics...")
    blocks: list[str] = []
    for purpose, cmd in RECON_PLAYBOOK:
        run.check_stop()
        res = await execute_command(run, cmd, actor="agent", purpose=purpose)
        blocks.append(f"### {purpose}\n$ {cmd}\n{res.output}".strip())
    return "\n\n".join(blocks)


async def _diagnose(run: Run, llm: LLM, recon: str) -> None:
    if not llm.configured:
        run.info(
            "No LLM configured (set OPENROUTER_API_KEY). Recon is complete; "
            "the technician can take over via the terminal."
        )
        return

    context = _ticket_context(run) + "\n\nRECON OUTPUT:\n" + recon
    for _ in range(MAX_HYPOTHESIS_ROUNDS):
        run.check_stop()
        hyps = await _hypotheses(run, llm, context)
        if not hyps:
            run.info("No further hypotheses produced.")
            return

        selection = await run.await_hypothesis_selection()
        hyp = _resolve_selection(run, selection)
        if hyp is None:
            continue
        hyp.status = "checking"
        _emit_hypotheses(run)
        run.audit.record("hypothesis_selected", text=hyp.title, source=hyp.source)
        origin = "your" if hyp.source == "technician" else "ranked"
        run.info(f"Checking {origin} hypothesis: {hyp.title}")

        run.set_phase(RunPhase.CHECK)
        # A hypothesis can carry several read-only checks; run them all and aggregate.
        check_blocks: list[str] = []
        for cmd in hyp.checks:
            res = await execute_command(run, cmd, actor="agent", purpose="hypothesis check")
            check_blocks.append(f"$ {cmd}\n{res.output}".strip())
        check_output = "\n\n".join(check_blocks) if check_blocks else "(no check command provided)"
        comment_block = _format_comments(hyp)

        verdict = await llm.complete_json(
            prompts.CHECK_SYSTEM,
            f"{context}\n\nSELECTED HYPOTHESIS: {hyp.title}\n{hyp.reasoning}\n{comment_block}\n\n"
            f"CHECK COMMANDS:\n{chr(10).join(hyp.checks) or '(none)'}\nCHECK OUTPUT:\n{check_output}",
            model=llm.agent_model,
            schema=CheckOutput,
        )

        if not verdict.get("confirmed"):
            hyp.status = "rejected"
            _emit_hypotheses(run)
            run.info(f"Hypothesis not confirmed: {verdict.get('reasoning', '')}")
            context += (
                f"\n\nREJECTED hypothesis '{hyp.title}'.{comment_block} Check output:\n{check_output}"
            )
            continue

        hyp.status = "confirmed"
        _emit_hypotheses(run)
        run.info(f"Root cause confirmed: {hyp.title}")
        fix = verdict.get("proposed_fix") or {}

        if not await _apply_fix(run, fix):
            context += f"\n\nFix for '{hyp.title}' was rejected or blocked."
            continue
        if not await _validate(run, llm, fix):
            context += f"\n\nFix for '{hyp.title}' applied but validation failed."
            continue

        await _persist_verify(run, llm, fix)
        await _finalize(run, llm)
        return

    run.info("Hypothesis limit reached without a validated fix. Technician can take over.")


async def _hypotheses(run: Run, llm: LLM, context: str) -> list[Hypothesis]:
    run.set_phase(RunPhase.HYPOTHESES)
    run.info("Forming ranked hypotheses...")
    data = await llm.complete_json(
        prompts.HYPOTHESES_SYSTEM, context, model=llm.agent_model, schema=HypothesesOutput
    )
    raw = data.get("hypotheses") or []
    hyps: list[Hypothesis] = []
    for i, h in enumerate(raw[:5], start=1):
        checks = [str(c).strip() for c in (h.get("proposed_checks") or []) if c and str(c).strip()]
        if not checks and h.get("proposed_check"):
            checks = [str(h["proposed_check"]).strip()]
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
    _normalize_likelihoods(hyps)
    run.hypotheses = hyps
    _emit_hypotheses(run)
    return hyps


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
        # Back-compat: a bare id string selects an existing hypothesis.
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


async def _apply_fix(run: Run, fix: dict) -> bool:
    commands = [c for c in (fix.get("commands") or []) if c and c.strip()]
    run.set_phase(RunPhase.FIX_PROPOSE)
    decision = await run.request_approval(
        "fix",
        {
            "explanation": fix.get("explanation", ""),
            "commands": commands,
            "service": fix.get("service"),
            "validation_command": fix.get("validation_command"),
        },
        "Apply the proposed fix",
    )
    if not decision.approved:
        run.info("Fix rejected by technician.")
        return False
    if decision.edited:
        commands = [c for c in decision.edited.splitlines() if c.strip()]

    run.set_phase(RunPhase.APPLY)
    for cmd in commands:
        res = await execute_command(run, cmd, actor="agent", purpose="apply fix", require_confirm=False)
        if res.blocked:
            run.info(f"Fix command blocked by safety layer: {res.reason}")
            return False
    return True


async def _validate(run: Run, llm: LLM, fix: dict) -> bool:
    run.set_phase(RunPhase.VALIDATE)
    vcmd = fix.get("validation_command")
    if not vcmd:
        run.info("No validation command proposed; treating apply as success.")
        return True
    res = await execute_command(run, vcmd, actor="agent", purpose="validate fix", require_confirm=False)
    verdict = await llm.complete_json(
        prompts.VALIDATION_SYSTEM,
        f"Validation command: {vcmd}\nOutput:\n{res.output}",
        model=llm.fast_model,
        schema=ValidationOutput,
    )
    success = bool(verdict.get("success"))
    proof = str(verdict.get("validation_result", ""))
    run.emit(EventType.VALIDATION, success=success, proof=proof, command=vcmd)
    run.audit.record("validation", text=proof, success=success)
    if not success:
        run.info("Validation did not confirm the fix.")
    return success


async def _persist_verify(run: Run, llm: LLM, fix: dict) -> None:
    run.set_phase(RunPhase.PERSIST_VERIFY)
    service = fix.get("service")
    if not service:
        run.info("No service identified for a persistence check.")
        return
    run.info(f"Verifying persistence by restarting {service}...")
    await execute_command(
        run, f"systemctl restart {shlex.quote(str(service))}",
        actor="agent", purpose="persistence restart", require_confirm=False,
    )
    vcmd = fix.get("validation_command")
    if not vcmd:
        return
    res = await execute_command(run, vcmd, actor="agent", purpose="re-validate after restart", require_confirm=False)
    verdict = await llm.complete_json(
        prompts.VALIDATION_SYSTEM,
        f"After restarting {service}. Command: {vcmd}\nOutput:\n{res.output}",
        model=llm.fast_model,
        schema=ValidationOutput,
    )
    success = bool(verdict.get("success"))
    proof = str(verdict.get("validation_result", ""))
    run.emit(EventType.VALIDATION, success=success, proof=proof, command=vcmd, after_restart=True)
    run.audit.record("validation", text=proof, success=success, after_restart=True)


async def _finalize(run: Run, llm: LLM) -> None:
    run.set_phase(RunPhase.ACTIVITY_DRAFT)
    run.info("Drafting activity documentation...")
    draft = await draft_activity(run, llm)
    run.activity_draft = draft
    run.emit(EventType.ACTIVITY_DRAFT, draft=draft)

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

    run.audit.record("activity_submitted", activity_id=getattr(created, "id", None))
    run.emit(EventType.ACTIVITY_SUBMITTED, activity_id=getattr(created, "id", None))
    await _safe_set_status(run, TicketStatus.DONE)
    run.set_phase(RunPhase.DONE)
    run.info("Activity submitted and ticket marked DONE.")
