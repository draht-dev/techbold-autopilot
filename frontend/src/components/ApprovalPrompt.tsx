import { useState } from "react";

export interface Approval {
  id: string;
  kind: string;
  purpose?: string;
  payload: any;
}

interface Props {
  approval: Approval;
  onDecide: (approved: boolean, edited?: string) => void;
}

export default function ApprovalPrompt({ approval, onDecide }: Props) {
  const { kind, payload, purpose } = approval;
  const [editedCommand, setEditedCommand] = useState<string>(payload?.command || "");
  const [editedFix, setEditedFix] = useState<string>(
    (payload?.commands || []).join("\n")
  );

  return (
    <div className="panel" style={{ borderColor: "var(--accent)" }}>
      <div className="panel-header">
        <h2>Approval required</h2>
        <span className="badge open">{kind}</span>
      </div>
      <div className="panel-body">
        {purpose && <p style={{ marginTop: 0 }}>{purpose}</p>}

        {kind === "connect" && (
          <dl className="kv">
            <dt>Host</dt>
            <dd className="mono">
              {payload.ip}:{payload.port}
            </dd>
            <dt>User</dt>
            <dd className="mono">{payload.username}</dd>
            <dt>OS</dt>
            <dd>{payload.os}</dd>
          </dl>
        )}

        {kind === "command" && (
          <div className="field">
            {payload.reason && (
              <div className="muted" style={{ marginBottom: 6 }}>
                Reason: {payload.reason}
              </div>
            )}
            <label>Command (you may edit before approving)</label>
            <textarea
              rows={2}
              value={editedCommand}
              onChange={(e) => setEditedCommand(e.target.value)}
            />
          </div>
        )}

        {kind === "fix" && (
          <div className="field">
            {payload.explanation && <p style={{ marginTop: 0 }}>{payload.explanation}</p>}
            {payload.service && (
              <div className="muted" style={{ fontSize: 12 }}>
                Persistence check service: <span className="mono">{payload.service}</span>
              </div>
            )}
            <label style={{ marginTop: 8 }}>Fix commands (one per line, editable)</label>
            <textarea
              rows={Math.min(8, Math.max(2, (payload.commands || []).length + 1))}
              value={editedFix}
              onChange={(e) => setEditedFix(e.target.value)}
            />
            {payload.validation_command && (
              <div className="mono" style={{ marginTop: 6, fontSize: 12 }}>
                validation: {payload.validation_command}
              </div>
            )}
            {payload.rollback && (
              <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
                rollback: {payload.rollback}
              </div>
            )}
          </div>
        )}

        <div className="btn-row" style={{ marginTop: 12 }}>
          <button
            className="primary"
            onClick={() => {
              if (kind === "command") onDecide(true, editedCommand.trim() || undefined);
              else if (kind === "fix") onDecide(true, editedFix.trim() || undefined);
              else onDecide(true);
            }}
          >
            Approve
          </button>
          <button className="danger" onClick={() => onDecide(false)}>
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}
