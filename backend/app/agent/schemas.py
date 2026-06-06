"""Pydantic schemas for LangChain structured LLM outputs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HypothesisItem(BaseModel):
    title: str
    reasoning: str = ""
    evidence: str = ""
    # One or more read-only commands that together confirm/deny this hypothesis.
    proposed_checks: list[str] = Field(default_factory=list)
    # Back-compat: some models still emit a single "proposed_check"; accepted too.
    proposed_check: str = ""
    # Probability this is the root cause (0-1); normalised to a % across the set.
    likelihood: Optional[float] = None


class HypothesesOutput(BaseModel):
    hypotheses: list[HypothesisItem] = Field(default_factory=list)


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
