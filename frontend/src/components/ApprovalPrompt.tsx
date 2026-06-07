import { useLayoutEffect, useRef, useState } from "react";

/**
 * A textarea that sizes itself to fit its content, so every command line is
 * visible without scrolling or manual resizing. Still vertically resizable by
 * the user (via the CSS `resize` handle) for very long fixes.
 */
function AutoTextarea({
  value,
  onChange,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  className?: string;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      className={className}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ overflow: "hidden" }}
    />
  );
}

export interface Approval {
  id: string;
  kind: string;
  purpose?: string;
  payload: any;
}

interface Props {
  approval: Approval;
  onDecide: (approved: boolean, edited?: string, reason?: string) => void;
}

export default function ApprovalPrompt({ approval, onDecide }: Props) {
  const { kind, payload, purpose } = approval;
  const [editedCommand, setEditedCommand] = useState<string>(payload?.command || "");
  const [editedFix, setEditedFix] = useState<string>(
    (payload?.commands || []).join("\n")
  );
  // Optional, opt-in reason the technician can attach when rejecting. Hidden behind
  // a subtle toggle so the default card stays clean and never demands an extra step.
  const [reason, setReason] = useState<string>("");
  const [showReason, setShowReason] = useState<boolean>(false);
  const canReason = kind === "command" || kind === "fix";

  function reject() {
    onDecide(false, undefined, reason.trim() || undefined);
  }

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
            <AutoTextarea value={editedCommand} onChange={setEditedCommand} />
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
            <AutoTextarea value={editedFix} onChange={setEditedFix} />
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

        {canReason &&
          (showReason ? (
            <div className="field" style={{ marginTop: 10, marginBottom: 0 }}>
              <input
                autoFocus
                type="text"
                value={reason}
                placeholder="Optional reason for rejecting…"
                onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") reject();
                }}
                style={{ fontSize: 12 }}
              />
              <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                Shared with the agent so it can adjust — leave blank to skip.
              </div>
            </div>
          ) : (
            <button
              className="ghost"
              style={{ marginTop: 10, fontSize: 12, padding: "2px 8px" }}
              onClick={() => setShowReason(true)}
            >
              + add a reason for rejecting
            </button>
          ))}

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
          <button className="danger" onClick={reject}>
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}
