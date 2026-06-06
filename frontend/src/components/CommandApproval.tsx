import React, { useState } from "react";
import { approve, reject } from "../api";
import type { AwaitingApprovalData } from "../types";

interface Props {
  sessionId: string;
  pending: AwaitingApprovalData;
  onDone: () => void;
}

export default function CommandApproval({ sessionId, pending, onDone }: Props) {
  const [editing, setEditing] = useState(false);
  const [editedCommand, setEditedCommand] = useState(pending.command);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleApprove(edited?: string) {
    setBusy(true);
    setError(null);
    try {
      await approve(sessionId, pending.action_id, edited);
      onDone();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  async function handleReject() {
    setBusy(true);
    setError(null);
    try {
      await reject(sessionId, pending.action_id);
      onDone();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="approval-card">
      <div className="approval-header">
        <span className="approval-title">Awaiting Approval</span>
        <span className={`badge badge-${pending.classification}`}>{pending.classification}</span>
      </div>

      <div className="approval-command">
        <code>{pending.command}</code>
      </div>

      <dl className="approval-details">
        <dt>Purpose</dt>
        <dd>{pending.purpose}</dd>
        <dt>Expected effect</dt>
        <dd>{pending.expected_effect}</dd>
        <dt>Rollback</dt>
        <dd>{pending.rollback || "—"}</dd>
      </dl>

      {editing && (
        <div className="approval-edit">
          <label>
            Edit command:
            <textarea
              className="command-edit-input"
              value={editedCommand}
              onChange={(e) => setEditedCommand(e.target.value)}
              rows={3}
            />
          </label>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      <div className="approval-actions">
        {!editing ? (
          <>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => handleApprove(undefined)}
            >
              Approve
            </button>
            <button
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              Edit &amp; Approve
            </button>
            <button
              className="btn btn-danger"
              disabled={busy}
              onClick={handleReject}
            >
              Reject
            </button>
          </>
        ) : (
          <>
            <button
              className="btn btn-primary"
              disabled={busy || editedCommand.trim() === ""}
              onClick={() => handleApprove(editedCommand.trim())}
            >
              Approve edited command
            </button>
            <button
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => { setEditing(false); setEditedCommand(pending.command); }}
            >
              Cancel edit
            </button>
            <button
              className="btn btn-danger"
              disabled={busy}
              onClick={handleReject}
            >
              Reject
            </button>
          </>
        )}
      </div>
    </div>
  );
}
