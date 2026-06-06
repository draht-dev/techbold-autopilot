"""Durable, ticket-keyed store for run resolutions.

A finished run's solution (the submitted activity) and full event log are written
here so the ticket page can show HOW a ticket was fixed *after* the run ends —
surviving in-memory garbage collection of old runs and backend restarts. The
ERP (Phoenix) accepts the activity but exposes no read-back, so this is the only
place the resolution can be retrieved from later.

One JSON file per ticket; the most recent resolution for a ticket wins.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ResolutionStore:
    def __init__(self, audit_dir: str = "audit_logs") -> None:
        self._dir = Path(audit_dir) / "resolutions"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, ticket_id: int) -> Path:
        return self._dir / f"{ticket_id}.json"

    def save(self, ticket_id: int, payload: dict[str, Any]) -> None:
        """Persist a resolution snapshot. Atomic (write-temp-then-replace) so a
        crash mid-write cannot leave a half-written file. Never raises — a failed
        persist must not crash a run (the in-memory copy still serves this process)."""
        path = self._path(ticket_id)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            # Don't crash the run, but make the failure visible — a silently
            # unwritable volume is exactly how "it's lost on restart" happens.
            logger.warning("Failed to persist resolution for ticket %s at %s: %s",
                           ticket_id, path, exc)

    def load(self, ticket_id: int) -> Optional[dict[str, Any]]:
        path = self._path(ticket_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
