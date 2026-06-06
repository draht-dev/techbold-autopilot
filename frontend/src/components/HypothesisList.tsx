import { useState } from "react";
import { Hypothesis } from "../api/client";

interface Props {
  hypotheses: Hypothesis[];
  selectable: boolean;
  compact?: boolean;
  onSelect: (id: string, comment?: string) => void;
  onSubmitCustom: (title: string, comment?: string) => void;
  onComment: (id: string, comment: string) => void;
}

function formatLikelihood(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const pct = value <= 1 ? Math.round(value * 100) : Math.round(value);
  return `${pct}%`;
}

function HypothesisCard({
  h,
  selectable,
  compact,
  onSelect,
  onComment,
}: {
  h: Hypothesis;
  selectable: boolean;
  compact?: boolean;
  onSelect: (id: string, comment?: string) => void;
  onComment: (id: string, comment: string) => void;
}) {
  const [comment, setComment] = useState(h.comment || "");

  return (
    <div
      className="notice"
      style={{
        borderColor:
          h.status === "confirmed"
            ? "var(--success)"
            : h.status === "rejected"
            ? "var(--border-strong)"
            : "var(--border)",
        opacity: h.status === "rejected" ? 0.6 : 1,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
        <strong>
          {h.rank > 0 ? `#${h.rank} · ` : ""}
          {h.title}
        </strong>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
          {h.likelihood != null && (
            <span className="badge open" style={{ fontSize: 11 }}>
              {formatLikelihood(h.likelihood)}
            </span>
          )}
          {h.source === "technician" && (
            <span className="muted" style={{ fontSize: 11 }}>
              yours
            </span>
          )}
          {h.status !== "open" && (
            <span className="muted" style={{ fontSize: 11 }}>
              {h.status}
            </span>
          )}
        </div>
      </div>
      {h.reasoning && <div style={{ marginTop: 4 }}>{h.reasoning}</div>}
      {!compact && h.evidence && (
        <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
          Evidence: {h.evidence}
        </div>
      )}
      {h.proposed_check && (
        <div className="mono" style={{ marginTop: 4, fontSize: 12 }}>
          check: {h.proposed_check}
        </div>
      )}
      {h.comment && !selectable && (
        <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
          Your note: {h.comment}
        </div>
      )}
      {selectable && (
        <>
          <div className="field" style={{ marginTop: 8, marginBottom: 0 }}>
            <label>Add a comment (optional)</label>
            <textarea
              rows={2}
              value={comment}
              placeholder="Context for the agent…"
              onChange={(e) => setComment(e.target.value)}
              onBlur={() => onComment(h.id, comment)}
            />
          </div>
          <div style={{ marginTop: 8 }}>
            <button className="primary" onClick={() => onSelect(h.id, comment.trim() || undefined)}>
              Check this hypothesis
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function HypothesisList({
  hypotheses,
  selectable,
  compact,
  onSelect,
  onSubmitCustom,
  onComment,
}: Props) {
  const [customTitle, setCustomTitle] = useState("");
  const [customComment, setCustomComment] = useState("");

  if (hypotheses.length === 0) return null;

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>{compact ? "Active Hypothesis" : "Ranked Hypotheses"}</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {selectable ? "select one or propose your own" : ""}
        </span>
      </div>
      <div className="panel-body">
        {hypotheses.map((h) => (
          <HypothesisCard
            key={h.id}
            h={h}
            selectable={selectable}
            compact={compact}
            onSelect={onSelect}
            onComment={onComment}
          />
        ))}

        {selectable && (
          <div className="notice" style={{ marginTop: 12, borderColor: "var(--accent)" }}>
            <strong>Propose your own hypothesis</strong>
            <div className="field" style={{ marginTop: 8, marginBottom: 0 }}>
              <label>Root-cause hypothesis</label>
              <textarea
                rows={2}
                value={customTitle}
                placeholder="Describe what you think is wrong…"
                onChange={(e) => setCustomTitle(e.target.value)}
              />
            </div>
            <div className="field" style={{ marginTop: 8, marginBottom: 0 }}>
              <label>Comment (optional)</label>
              <textarea
                rows={2}
                value={customComment}
                placeholder="Why you suspect this…"
                onChange={(e) => setCustomComment(e.target.value)}
              />
            </div>
            <div style={{ marginTop: 8 }}>
              <button
                className="primary"
                disabled={!customTitle.trim()}
                onClick={() => {
                  onSubmitCustom(
                    customTitle.trim(),
                    customComment.trim() || undefined
                  );
                  setCustomTitle("");
                  setCustomComment("");
                }}
              >
                Check my hypothesis
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
