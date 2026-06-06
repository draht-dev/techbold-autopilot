import { useState } from "react";
import { Hypothesis } from "../api/client";

interface Props {
  hypotheses: Hypothesis[];
  /** "select": full ranked list with actions. "active": read-only single hypothesis. */
  mode: "select" | "active";
  onSelect: (id: string) => void;
  onComment: (id: string, text: string) => void;
  onSubmitOwn: (h: { title: string; reasoning: string; checks: string[] }) => void;
}

export default function HypothesisList({
  hypotheses,
  mode,
  onSelect,
  onComment,
  onSubmitOwn,
}: Props) {
  const selectable = mode === "select";
  if (hypotheses.length === 0 && !selectable) return null;

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>{selectable ? "Ranked Hypotheses" : "Hypothesis under test"}</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {selectable ? "pick one, comment, or write your own" : "checking…"}
        </span>
      </div>
      <div className="panel-body">
        {hypotheses.map((h) => (
          <HypothesisCard
            key={h.id}
            h={h}
            selectable={selectable}
            onSelect={onSelect}
            onComment={onComment}
          />
        ))}
        {selectable && <OwnHypothesisForm onSubmitOwn={onSubmitOwn} />}
      </div>
    </div>
  );
}

function HypothesisCard({
  h,
  selectable,
  onSelect,
  onComment,
}: {
  h: Hypothesis;
  selectable: boolean;
  onSelect: (id: string) => void;
  onComment: (id: string, text: string) => void;
}) {
  const [comment, setComment] = useState("");
  const pct = h.likelihood != null ? Math.round(h.likelihood) : null;
  const borderColor =
    h.status === "confirmed"
      ? "var(--success)"
      : h.status === "rejected"
      ? "var(--border-strong)"
      : h.status === "checking"
      ? "var(--accent)"
      : "var(--border)";

  function addComment() {
    const text = comment.trim();
    if (!text) return;
    onComment(h.id, text);
    setComment("");
  }

  return (
    <div className="notice" style={{ borderColor, opacity: h.status === "rejected" ? 0.6 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <strong>
          #{h.rank} · {h.title}
        </strong>
        {h.source === "technician" ? (
          <span className="badge open" title="Your hypothesis">
            yours
          </span>
        ) : (
          h.status !== "open" && (
            <span className="muted" style={{ fontSize: 11 }}>
              {h.status}
            </span>
          )
        )}
      </div>

      {/* Likelihood as a percentage bar (relative confidence, not a fixed score). */}
      {pct != null && (
        <div className="likelihood" title={`Estimated likelihood ${pct}%`}>
          <div className="likelihood-bar">
            <span style={{ width: `${pct}%` }} />
          </div>
          <span className="likelihood-pct">{pct}%</span>
        </div>
      )}

      {h.reasoning && <div style={{ marginTop: 6 }}>{h.reasoning}</div>}
      {h.evidence && (
        <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
          Evidence: {h.evidence}
        </div>
      )}

      {h.checks?.length > 0 && (
        <div className="mono" style={{ marginTop: 6, fontSize: 12 }}>
          {h.checks.map((c, i) => (
            <div key={i}>check: {c}</div>
          ))}
        </div>
      )}

      {h.comments?.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {h.comments.map((c, i) => (
            <div key={i} className="comment">
              💬 {c.text}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <input
          type="text"
          placeholder="Add a comment to steer the agent…"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") addComment();
          }}
        />
        <button className="ghost" onClick={addComment} disabled={!comment.trim()}>
          Comment
        </button>
      </div>

      {selectable && (
        <div style={{ marginTop: 8 }}>
          <button className="primary" onClick={() => onSelect(h.id)}>
            Check this hypothesis
          </button>
        </div>
      )}
    </div>
  );
}

function OwnHypothesisForm({
  onSubmitOwn,
}: {
  onSubmitOwn: (h: { title: string; reasoning: string; checks: string[] }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [checks, setChecks] = useState("");

  function submit() {
    if (!title.trim()) return;
    onSubmitOwn({
      title: title.trim(),
      reasoning: reasoning.trim(),
      checks: checks
        .split("\n")
        .map((c) => c.trim())
        .filter(Boolean),
    });
    setTitle("");
    setReasoning("");
    setChecks("");
    setOpen(false);
  }

  if (!open) {
    return (
      <button className="ghost" style={{ marginTop: 4 }} onClick={() => setOpen(true)}>
        ✎ Suggest your own hypothesis
      </button>
    );
  }

  return (
    <div className="notice" style={{ borderColor: "var(--accent)" }}>
      <strong>Your hypothesis</strong>
      <div className="field" style={{ marginTop: 6 }}>
        <label>Root-cause statement</label>
        <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
      </div>
      <div className="field">
        <label>Reasoning (optional)</label>
        <textarea rows={2} value={reasoning} onChange={(e) => setReasoning(e.target.value)} />
      </div>
      <div className="field">
        <label>Check commands — one per line (optional, read-only)</label>
        <textarea
          rows={2}
          className="mono"
          placeholder={"systemctl status nginx\njournalctl -u nginx -n 50"}
          value={checks}
          onChange={(e) => setChecks(e.target.value)}
        />
      </div>
      <div className="btn-row">
        <button className="primary" onClick={submit} disabled={!title.trim()}>
          Suggest &amp; check
        </button>
        <button className="ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
