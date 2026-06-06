"""Tests for the activity_generator module — SPEC §12.

Asserts:
  1. All 8 documentation-relevant fields are populated and non-empty.
  2. ALL fields are SECRET-FREE: a secret embedded in command/output_summary
     must NOT appear in any field of the resulting activity.
  3. When final=None, fields are derived from the audit log and non-empty;
     actions_taken is ordered/numbered; validation_result is derived.
  4. When final IS provided, its values are used (but still redacted).

Uses the real AuditLog (no mocks) with a temp audit directory.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app.activity_generator import build_activity
from app.audit_log import AuditLog
from app.models import ActivityCreate, FinalReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUDIT_DIR = "/tmp/techbold-actgen-test"

_TICKET_ID = 7001
_START = "2026-06-07T10:00:00Z"
_END = "2026-06-07T10:25:00Z"


def _make_audit_log(session_id: str) -> AuditLog:
    """Build an AuditLog in the test audit directory."""
    return AuditLog(session_id=session_id, audit_dir=_AUDIT_DIR)


def _all_string_fields(activity: ActivityCreate) -> list[str]:
    """Return all string-valued fields from the activity."""
    return [
        activity.summary,
        activity.root_cause,
        activity.actions_taken,
        activity.commands_summary,
        activity.validation_result,
        activity.description,
        activity.start_datetime,
        activity.end_datetime,
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildActivityNoFinal:
    """build_activity with final=None — fields must be derived from audit log."""

    def setup_method(self) -> None:
        """Build a populated AuditLog with entries across diagnose/apply/validate."""
        self.log = _make_audit_log("test-no-final")
        # Phase: diagnose
        self.log.append(
            phase="diagnose",
            command="systemctl status nginx",
            classification="read_only",
            approval="auto",
            exit_code=3,
            output_summary="nginx inactive (dead) — not running",
            agent_rationale="Checking whether nginx is running and enabled",
        )
        self.log.append(
            phase="diagnose",
            command="systemctl is-enabled nginx",
            classification="read_only",
            approval="auto",
            exit_code=1,
            output_summary="disabled",
            agent_rationale="Confirming nginx is disabled at boot",
        )
        # Phase: apply
        self.log.append(
            phase="apply",
            command="sudo systemctl enable --now nginx",
            classification="needs_approval",
            approval="approved",
            approved_by="technician",
            exit_code=0,
            output_summary="Created symlink; nginx active",
            agent_rationale="Enable nginx and start it immediately",
        )
        # Phase: validate
        self.log.append(
            phase="validate",
            command="systemctl is-active nginx",
            classification="read_only",
            approval="auto",
            exit_code=0,
            output_summary="active",
            agent_rationale="Verifying nginx is now active",
        )
        self.log.append(
            phase="persist_check",
            command="systemctl is-enabled nginx",
            classification="read_only",
            approval="auto",
            exit_code=0,
            output_summary="enabled",
            agent_rationale="Verifying nginx is enabled for boot persistence",
        )

    def test_all_fields_populated(self) -> None:
        """All 8 documentation-relevant fields must be non-empty."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        assert isinstance(activity, ActivityCreate)
        assert activity.ticket_id == _TICKET_ID
        assert activity.start_datetime == _START
        assert activity.end_datetime == _END
        assert activity.summary.strip(), "summary must not be empty"
        assert activity.root_cause.strip(), "root_cause must not be empty"
        assert activity.actions_taken.strip(), "actions_taken must not be empty"
        assert activity.commands_summary.strip(), "commands_summary must not be empty"
        assert activity.validation_result.strip(), "validation_result must not be empty"
        assert activity.description.strip(), "description must not be empty"

    def test_actions_taken_ordered_and_numbered(self) -> None:
        """actions_taken must be an ordered numbered list of executed entries."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        lines = [l.strip() for l in activity.actions_taken.splitlines() if l.strip()]
        assert len(lines) >= 1, "actions_taken must have at least one line"
        # First line must start with "1."
        assert lines[0].startswith("1."), f"Expected '1.' at start, got: {lines[0]!r}"
        # Check consecutive numbering
        for i, line in enumerate(lines, start=1):
            assert line.startswith(f"{i}."), (
                f"Expected line {i} to start with '{i}.', got: {line!r}"
            )

    def test_validation_result_derived(self) -> None:
        """validation_result must reference validate/persist_check phase evidence."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        # Should contain evidence from the validate/persist_check phases
        val = activity.validation_result
        assert val.strip(), "validation_result must not be empty"
        # Should mention is-active or is-enabled or contain validation commands
        val_lower = val.lower()
        assert any(
            keyword in val_lower
            for keyword in ("nginx", "active", "enabled", "pass", "exit=0")
        ), f"validation_result appears unrelated to validate evidence: {val!r}"

    def test_commands_summary_contains_executed_commands(self) -> None:
        """commands_summary must reference the commands actually run."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        cs = activity.commands_summary
        # Should mention nginx somewhere
        assert "nginx" in cs, f"commands_summary should mention nginx, got: {cs!r}"


class TestBuildActivityWithFinal:
    """build_activity with a FinalReport provided — its values are used."""

    def setup_method(self) -> None:
        self.log = _make_audit_log("test-with-final")
        self.log.append(
            phase="diagnose",
            command="journalctl -u postgresql --no-pager -n 50",
            classification="read_only",
            approval="auto",
            exit_code=0,
            output_summary="connection refused on port 5432",
            agent_rationale="Checking postgresql recent logs",
        )
        self.log.append(
            phase="apply",
            command="sudo systemctl enable --now postgresql",
            classification="needs_approval",
            approval="approved",
            approved_by="technician",
            exit_code=0,
            output_summary="postgresql enabled and started",
            agent_rationale="Enable postgresql and start immediately",
        )
        self.log.append(
            phase="validate",
            command="systemctl is-active postgresql",
            classification="read_only",
            approval="auto",
            exit_code=0,
            output_summary="active",
            agent_rationale="Confirming postgresql is running",
        )

        self.final = FinalReport(
            root_cause="postgresql unit was disabled and not started after package installation",
            summary="Restored PostgreSQL database service for the customer.",
            actions_taken=(
                "1. Checked postgresql status and logs.\n"
                "2. Ran systemctl enable --now postgresql to enable and start it.\n"
                "3. Validated service is active and enabled."
            ),
            commands_summary=(
                "journalctl -u postgresql | systemctl enable --now postgresql | systemctl is-active postgresql"
            ),
            validation_result="systemctl is-active postgresql returns 'active'; port 5432 listening.",
        )

    def test_final_values_used(self) -> None:
        """When final is provided its values appear in the activity."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
            final=self.final,
        )
        assert "PostgreSQL" in activity.summary or "postgresql" in activity.summary.lower()
        assert "postgresql unit was disabled" in activity.root_cause.lower() or \
               "disabled" in activity.root_cause.lower()
        assert "5432" in activity.validation_result or "active" in activity.validation_result

    def test_all_fields_still_populated(self) -> None:
        """Even with final present, all fields must remain non-empty."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
            final=self.final,
        )
        assert activity.summary.strip()
        assert activity.root_cause.strip()
        assert activity.actions_taken.strip()
        assert activity.commands_summary.strip()
        assert activity.validation_result.strip()
        assert activity.description.strip()


class TestSecretRedaction:
    """All fields must be SECRET-FREE — SPEC §2.1 + §4.3."""

    SECRET = "hunter2"

    def setup_method(self) -> None:
        """Build an AuditLog with a secret embedded in command + output_summary."""
        self.log = _make_audit_log("test-secret-redaction")
        # Entry whose command contains a secret (DB URI with password)
        self.log.append(
            phase="diagnose",
            command=f"psql 'postgres://admin:{self.SECRET}@db/app' -c '\\l'",
            classification="read_only",
            approval="auto",
            exit_code=0,
            output_summary=f"PGPASSWORD={self.SECRET} connected to db/app",
            agent_rationale="Checking database connectivity",
        )
        # A validate entry with a secret in rationale
        self.log.append(
            phase="validate",
            command="psql -c 'SELECT 1'",
            classification="read_only",
            approval="auto",
            exit_code=0,
            output_summary=f"Connection OK using password {self.SECRET}",
            agent_rationale=f"Verified DB connection with password={self.SECRET}",
        )

    def _assert_no_secret(self, activity: ActivityCreate) -> None:
        for field_value in _all_string_fields(activity):
            assert self.SECRET not in field_value, (
                f"Secret '{self.SECRET}' found in activity field: {field_value!r}"
            )

    def test_no_secret_in_any_field_no_final(self) -> None:
        """Secret must not appear in any field when final=None."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        self._assert_no_secret(activity)

    def test_no_secret_in_any_field_with_final(self) -> None:
        """Secret must not appear in any field even when final contains it in structured patterns."""
        # Use structured secret patterns that redact() is designed to catch:
        #   - DB URI with credentials (postgres://user:pass@host)
        #   - Env-var assignment (PGPASSWORD=hunter2, PASSWORD=hunter2)
        final = FinalReport(
            root_cause=f"App could not connect: PGPASSWORD={self.SECRET} was set incorrectly",
            summary=f"Restored DB connection. Used psql 'postgres://admin:{self.SECRET}@db/app'.",
            actions_taken=f"1. Ran psql with PGPASSWORD={self.SECRET} to confirm access.",
            commands_summary=f"psql 'postgres://admin:{self.SECRET}@db/app'",
            validation_result=f"Connection succeeded with PGPASSWORD={self.SECRET}",
        )
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
            final=final,
        )
        self._assert_no_secret(activity)

    def test_commands_summary_secret_free(self) -> None:
        """commands_summary specifically must be secret-free."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        assert self.SECRET not in activity.commands_summary, (
            f"Secret found in commands_summary: {activity.commands_summary!r}"
        )

    def test_all_fields_still_populated_after_redaction(self) -> None:
        """Redaction must not empty any field."""
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=self.log,
        )
        assert activity.summary.strip()
        assert activity.root_cause.strip()
        assert activity.actions_taken.strip()
        assert activity.commands_summary.strip()
        assert activity.validation_result.strip()
        assert activity.description.strip()


class TestBuildActivityEmptyLog:
    """build_activity with an empty audit log — must still produce non-empty fields."""

    def test_all_fields_populated_empty_log(self) -> None:
        log = _make_audit_log("test-empty-log")
        activity = build_activity(
            ticket_id=_TICKET_ID,
            start_datetime=_START,
            end_datetime=_END,
            audit_log=log,
        )
        assert activity.ticket_id == _TICKET_ID
        assert activity.summary.strip()
        assert activity.root_cause.strip()
        assert activity.actions_taken.strip()
        assert activity.commands_summary.strip()
        assert activity.validation_result.strip()
        assert activity.description.strip()


class TestBuildActivityPassThrough:
    """ticket_id, start_datetime, end_datetime pass through unmodified."""

    def test_passthrough_fields(self) -> None:
        log = _make_audit_log("test-passthrough")
        activity = build_activity(
            ticket_id=9999,
            start_datetime="2026-01-01T08:00:00Z",
            end_datetime="2026-01-01T09:00:00Z",
            audit_log=log,
        )
        assert activity.ticket_id == 9999
        assert activity.start_datetime == "2026-01-01T08:00:00Z"
        assert activity.end_datetime == "2026-01-01T09:00:00Z"
