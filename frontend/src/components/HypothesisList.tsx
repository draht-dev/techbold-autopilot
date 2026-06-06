import { Hypothesis } from "../api/client";

interface Props {
  hypotheses: Hypothesis[];
  selectable: boolean;
  onSelect: (id: string) => void;
}

export default function HypothesisList({ hypotheses, selectable, onSelect }: Props) {
  if (hypotheses.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Ranked Hypotheses</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {selectable ? "select one to check" : ""}
        </span>
      </div>
      <div className="panel-body">
        {hypotheses.map((h) => (
          <div
            key={h.id}
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
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <strong>
                #{h.rank} · {h.title}
              </strong>
              {h.status !== "open" && (
                <span className="muted" style={{ fontSize: 11 }}>
                  {h.status}
                </span>
              )}
            </div>
            {h.reasoning && <div style={{ marginTop: 4 }}>{h.reasoning}</div>}
            {h.evidence && (
              <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
                Evidence: {h.evidence}
              </div>
            )}
            {h.proposed_check && (
              <div className="mono" style={{ marginTop: 4, fontSize: 12 }}>
                check: {h.proposed_check}
              </div>
            )}
            {selectable && (
              <div style={{ marginTop: 8 }}>
                <button className="primary" onClick={() => onSelect(h.id)}>
                  Check this hypothesis
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
