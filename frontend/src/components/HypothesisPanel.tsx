import React from "react";
import type { HypothesisItem } from "../types";

interface Props {
  hypotheses: HypothesisItem[];
}

function confidenceColor(conf: number): string {
  if (conf >= 0.75) return "#16a34a";
  if (conf >= 0.45) return "#d97706";
  return "#dc2626";
}

export default function HypothesisPanel({ hypotheses }: Props) {
  if (hypotheses.length === 0) {
    return (
      <div className="panel hypothesis-panel">
        <h4>Hypotheses</h4>
        <p className="state-info">Awaiting diagnosis…</p>
      </div>
    );
  }

  return (
    <div className="panel hypothesis-panel">
      <h4>Hypotheses</h4>
      <ol className="hypothesis-list">
        {hypotheses.map((h, i) => (
          <li key={i} className="hypothesis-item">
            <div className="hypothesis-header">
              <span className="hypothesis-cause">{h.cause}</span>
              <span
                className="hypothesis-confidence"
                style={{ color: confidenceColor(h.confidence) }}
              >
                {Math.round(h.confidence * 100)}%
              </span>
            </div>
            <div className="confidence-bar-track">
              <div
                className="confidence-bar-fill"
                style={{
                  width: `${Math.round(h.confidence * 100)}%`,
                  backgroundColor: confidenceColor(h.confidence),
                }}
              />
            </div>
            <p className="hypothesis-evidence">{h.evidence}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
