import React, { useState } from "react";
import { submitActivity } from "../api";
import type { Activity } from "../types";

interface Props {
  sessionId: string;
  draft: Activity;
  onSubmitted: () => void;
}

export default function ActivityReview({ sessionId, draft, onSubmitted }: Props) {
  const [form, setForm] = useState<Activity>({ ...draft });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function update(field: keyof Activity, value: string | number) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await submitActivity(sessionId, form);
      setSuccess(true);
      onSubmitted();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  if (success) {
    return (
      <div className="panel activity-panel">
        <div className="success-banner">
          Activity submitted successfully. Ticket set to DONE.
        </div>
      </div>
    );
  }

  return (
    <div className="panel activity-panel">
      <h3>Review &amp; Submit Activity</h3>
      <p className="state-info">
        Review and edit the AI-generated activity record below before submitting to the ERP.
      </p>

      <form onSubmit={handleSubmit} className="activity-form">
        <label className="form-field">
          <span>Ticket ID</span>
          <input
            type="number"
            value={form.ticket_id}
            onChange={(e) => update("ticket_id", parseInt(e.target.value, 10))}
          />
        </label>

        <label className="form-field">
          <span>Start Datetime</span>
          <input
            type="text"
            value={form.start_datetime}
            onChange={(e) => update("start_datetime", e.target.value)}
          />
        </label>

        <label className="form-field">
          <span>End Datetime</span>
          <input
            type="text"
            value={form.end_datetime}
            onChange={(e) => update("end_datetime", e.target.value)}
          />
        </label>

        <label className="form-field">
          <span>Summary</span>
          <textarea
            rows={2}
            value={form.summary}
            onChange={(e) => update("summary", e.target.value)}
          />
        </label>

        <label className="form-field">
          <span>Root Cause</span>
          <textarea
            rows={3}
            value={form.root_cause}
            onChange={(e) => update("root_cause", e.target.value)}
          />
        </label>

        <label className="form-field">
          <span>Actions Taken</span>
          <textarea
            rows={5}
            value={form.actions_taken}
            onChange={(e) => update("actions_taken", e.target.value)}
          />
        </label>

        <label className="form-field">
          <span>Commands Summary (no secrets)</span>
          <textarea
            rows={4}
            value={form.commands_summary}
            onChange={(e) => update("commands_summary", e.target.value)}
          />
        </label>

        <label className="form-field">
          <span>Validation Result</span>
          <textarea
            rows={3}
            value={form.validation_result}
            onChange={(e) => update("validation_result", e.target.value)}
          />
        </label>

        {form.description !== undefined && (
          <label className="form-field">
            <span>Description</span>
            <textarea
              rows={3}
              value={form.description ?? ""}
              onChange={(e) => update("description", e.target.value)}
            />
          </label>
        )}

        {error && <div className="error-banner">{error}</div>}

        <div className="form-actions">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting}
          >
            {submitting ? "Submitting…" : "Submit activity & mark DONE"}
          </button>
        </div>
      </form>
    </div>
  );
}
