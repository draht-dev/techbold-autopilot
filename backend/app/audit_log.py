"""Audit log — SPEC §6.

Append-only, per-session, in-memory + JSONL on disk.  One entry per action.
This is the single source of truth for the graded activity (category C).

Never write raw secrets here — every field is passed through ``redact()``
before storage.  A disk-write failure is warned but never raised; the session
loop must not crash on a logging failure (SPEC §13).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.models import Approval, AuditEntry
from app.safety.redaction import redact

logger = logging.getLogger("app.audit_log")


class AuditLog:
    """Append-only structured session log (memory + JSONL file)."""

    def __init__(self, session_id: str, audit_dir: Optional[str] = None) -> None:
        resolved_dir = audit_dir if audit_dir is not None else get_settings().audit_dir
        self._path = Path(resolved_dir) / f"{session_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[AuditEntry] = []

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        phase: str,
        command: str,
        classification: str,
        approval: str,
        approved_by: Optional[str] = None,
        exit_code: Optional[int] = None,
        output_summary: str = "",
        agent_rationale: str = "",
        ts: Optional[str] = None,
    ) -> AuditEntry:
        """Build, redact, persist, and return an ``AuditEntry``."""
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()

        entry = AuditEntry(
            ts=ts,
            phase=phase,
            command=redact(command),
            classification=classification,
            approval=approval,
            approved_by=approved_by,
            exit_code=exit_code,
            output_summary=redact(output_summary),
            agent_rationale=redact(agent_rationale),
        )
        self._persist(entry)
        self._entries.append(entry)
        return entry

    def append_entry(self, entry: AuditEntry) -> AuditEntry:
        """Redact and persist a pre-built ``AuditEntry`` (same guarantees)."""
        # Re-build with redacted fields to guarantee no secrets leak.
        safe_entry = AuditEntry(
            ts=entry.ts,
            phase=entry.phase,
            command=redact(entry.command),
            classification=entry.classification,
            approval=entry.approval,
            approved_by=entry.approved_by,
            exit_code=entry.exit_code,
            output_summary=redact(entry.output_summary),
            agent_rationale=redact(entry.agent_rationale),
        )
        self._persist(safe_entry)
        self._entries.append(safe_entry)
        return safe_entry

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[AuditEntry]:
        """Return a shallow copy of the in-memory entry list."""
        return list(self._entries)

    def to_dicts(self) -> list[dict]:
        """Return all entries serialized as plain dicts (for JSON responses)."""
        return [e.model_dump() for e in self._entries]

    # ------------------------------------------------------------------
    # Helper accessors used by the activity generator (SPEC §11/§2.1)
    # ------------------------------------------------------------------

    def commands(self) -> list[str]:
        """All logged commands in order."""
        return [e.command for e in self._entries]

    def executed_entries(self) -> list[AuditEntry]:
        """Entries where approval is in {auto, approved, edited} AND exit_code is not None."""
        executed_approvals = {
            Approval.AUTO.value,
            Approval.APPROVED.value,
            Approval.EDITED.value,
        }
        return [
            e
            for e in self._entries
            if e.approval in executed_approvals and e.exit_code is not None
        ]

    def rejected_or_blocked(self) -> list[AuditEntry]:
        """Entries where approval is rejected or hard_block."""
        blocked_approvals = {Approval.REJECTED.value, Approval.HARD_BLOCK.value}
        return [e for e in self._entries if e.approval in blocked_approvals]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist(self, entry: AuditEntry) -> None:
        """Append one JSON line to the JSONL file; warn on failure, never raise."""
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.model_dump()) + "\n")
                fh.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "audit_log: failed to write entry to %s: %s", self._path, exc
            )
