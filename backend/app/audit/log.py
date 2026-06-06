"""Append-only audit log (one JSONL file per run).

Every command and key action is recorded here. Output is redacted *before* it is
persisted so secrets never touch disk. The same in-memory list is the raw
material the activity generator summarises from.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import utcnow_iso
from app.safety import redact

# Fields that may contain command output and must be scrubbed before persist.
_TEXT_FIELDS = ("output", "stdout", "stderr", "combined", "result")


class AuditLog:
    def __init__(self, run_id: str, audit_dir: str = "audit_logs") -> None:
        self.run_id = run_id
        self.entries: list[dict[str, Any]] = []
        self._dir = Path(audit_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.path = self._dir / f"{run_id}.jsonl"

    def record(self, type_: str, **fields: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"ts": utcnow_iso(), "run_id": self.run_id, "type": type_}
        redactions = 0
        for key, value in fields.items():
            if key in _TEXT_FIELDS and isinstance(value, str):
                scrubbed, n = redact(value)
                entry[key] = scrubbed
                redactions += n
            else:
                entry[key] = value
        if redactions:
            entry["redactions"] = redactions
        self.entries.append(entry)
        self._persist(entry)
        return entry

    def _persist(self, entry: dict[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            # Never let an audit write crash a run; in-memory copy still exists.
            pass

    def commands(self) -> list[dict[str, Any]]:
        return [e for e in self.entries if e.get("type") == "command"]

    def as_transcript(self) -> str:
        """Compact human-readable transcript for the activity generator."""
        lines: list[str] = []
        for e in self.entries:
            t = e.get("type")
            if t == "command":
                lines.append(
                    f"$ {e.get('command', '')}  (exit={e.get('exit_code')}, by={e.get('actor')})"
                )
                out = (e.get("output") or "").strip()
                if out:
                    lines.append(out[:2000])
            elif t in ("hypothesis_selected", "validation", "note", "phase"):
                lines.append(f"[{t}] {e.get('text') or e.get('detail') or ''}")
        return "\n".join(lines)
