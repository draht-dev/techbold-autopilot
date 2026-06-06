"""Pydantic schemas for LangChain structured LLM outputs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HypothesisItem(BaseModel):
    title: str
    reasoning: str = ""
    evidence: str = ""
    proposed_check: str = ""
    likelihood: Optional[float] = None


class HypothesesOutput(BaseModel):
    hypotheses: list[HypothesisItem] = Field(default_factory=list)


class CheckCommandOutput(BaseModel):
    """A single read-only command to confirm/deny a technician-supplied hypothesis."""

    proposed_check: str = ""


class ProposedFix(BaseModel):
    explanation: str = ""
    commands: list[str] = Field(default_factory=list)
    service: Optional[str] = None
    validation_command: Optional[str] = None


class CheckOutput(BaseModel):
    confirmed: bool
    reasoning: str = ""
    proposed_fix: Optional[ProposedFix] = None


class ValidationOutput(BaseModel):
    success: bool
    validation_result: str = ""


class ActivityOutput(BaseModel):
    summary: str = ""
    root_cause: str = ""
    actions_taken: str = ""
    commands_summary: str = ""
    validation_result: str = ""
    description: str = ""
