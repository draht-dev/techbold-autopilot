import { AgentDecision } from "../api/client";

interface Props {
  decision: AgentDecision;
  onChoose: (choice: string) => void;
}

/**
 * Blocking prompt shown when the agent calls RequestDecision — e.g. it could not
 * reproduce the reported bug and needs the technician to decide how to proceed.
 */
export default function DecisionPrompt({ decision, onChoose }: Props) {
  return (
    <div className="panel" style={{ borderColor: "var(--accent)" }}>
      <div className="panel-header">
        <h2>Decision needed</h2>
        <span className="badge open">agent</span>
      </div>
      <div className="panel-body">
        <p style={{ marginTop: 0 }}>{decision.question}</p>
        {decision.context && (
          <div className="muted" style={{ marginBottom: 8, fontSize: 13, whiteSpace: "pre-wrap" }}>
            {decision.context}
          </div>
        )}
        <div className="btn-row" style={{ flexWrap: "wrap", gap: 8 }}>
          {decision.options.map((opt, i) => (
            <button
              key={i}
              className={i === 0 ? "primary" : "ghost"}
              onClick={() => onChoose(opt)}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
