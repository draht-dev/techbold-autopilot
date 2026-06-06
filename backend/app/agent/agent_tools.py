"""Tool schemas the autonomous agent can call.

These are Pydantic models bound to the model via ``bind_tools``; the class name is
the tool name the model emits, and the docstring is the tool description the model
sees. The loop in ``app.agent.loop`` dispatches each tool call by name and runs the
real side effect (always through the gated, audited, redacted command path or a
human-in-the-loop gate). The model never touches SSH or the ERP directly.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# The tool names the model will emit (kept in one place for the dispatcher).
RUN_COMMAND = "RunCommand"
PRESENT_HYPOTHESES = "PresentHypotheses"
REQUEST_DECISION = "RequestDecision"
FINISH = "Finish"


class RunCommand(BaseModel):
    """Run ONE shell command on the customer's Linux VM over SSH.

    Use this for everything: reproducing the problem, read-only diagnostics, and
    applying the fix. Read-only commands may run automatically; any command that
    changes state is shown to the technician for approval before it runs, and
    dangerous commands are blocked outright. Command output is redacted of
    secrets before you see it. Prefer minimal, targeted commands and never try to
    read or print secret files.
    """

    command: str = Field(description="The exact shell command to run on the VM.")
    purpose: str = Field(
        default="",
        description="A short reason for this command (shown to the technician).",
    )


class HypothesisInput(BaseModel):
    """A single ranked root-cause hypothesis."""

    title: str = Field(description="Short root-cause statement.")
    reasoning: str = Field(default="", description="Why this is plausible, tied to evidence.")
    evidence: str = Field(default="", description="The specific finding(s) that point here.")
    proposed_checks: list[str] = Field(
        default_factory=list,
        description="One or more read-only commands that would confirm/deny this.",
    )
    likelihood: Optional[float] = Field(
        default=None,
        description="Your probability (0.0-1.0) that this is the actual root cause.",
    )


class PresentHypotheses(BaseModel):
    """Present a RANKED list of root-cause hypotheses to the technician and WAIT.

    Call this once you have enough evidence to propose candidate root causes
    (typically after reproducing the problem). The run pauses until the technician
    picks one, writes their own, and/or leaves steering comments; the selection is
    returned to you so you can investigate it. You can call this again later with a
    fresh, revised list whenever your investigation changes the picture.
    """

    hypotheses: list[HypothesisInput] = Field(
        description="2-5 candidate root causes, most likely first.",
    )


class RequestDecision(BaseModel):
    """Ask the technician to make a decision when you are blocked or uncertain.

    Use this when you cannot reproduce the reported problem, when there are
    materially different paths forward, or when a judgement call is needed that the
    technician should own. The run pauses until they choose one of your options,
    and their choice is returned to you.
    """

    question: str = Field(description="The decision to make, phrased clearly.")
    options: list[str] = Field(description="The distinct choices the technician can pick from.")
    context: str = Field(default="", description="Optional supporting detail for the decision.")


class Finish(BaseModel):
    """End the run and hand off the documentation to the technician.

    Call this when the incident is resolved and validated, when the technician has
    decided to close it as not-reproducible, or when it must be escalated. A draft
    activity is generated from the run transcript for the technician to review and
    submit.
    """

    outcome: Literal["fixed", "not_reproducible", "escalate"] = Field(
        description="The final outcome of the run.",
    )
    note: str = Field(
        default="",
        description="A short closing note: what was done / why it is being closed.",
    )


AGENT_TOOLS = [RunCommand, PresentHypotheses, RequestDecision, Finish]
