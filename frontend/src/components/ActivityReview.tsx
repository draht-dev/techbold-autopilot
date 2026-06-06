import { useState } from "react";
import { ActivityDraft } from "../api/client";

interface Props {
  draft: ActivityDraft;
  submittedId: number | null;
  onSubmit: (edited: ActivityDraft) => void;
}

const FIELDS: { key: keyof ActivityDraft; label: string; rows: number }[] = [
  { key: "summary", label: "Summary (one sentence)", rows: 2 },
  { key: "root_cause", label: "Root cause (technical, not symptom)", rows: 2 },
  { key: "actions_taken", label: "Actions taken (in order)", rows: 4 },
  { key: "commands_summary", label: "Commands summary (no secrets)", rows: 3 },
  { key: "validation_result", label: "Validation result (concrete proof)", rows: 3 },
  { key: "description", label: "Description", rows: 3 },
];

export default function ActivityReview({ draft, submittedId, onSubmit }: Props) {
  const [fields, setFields] = useState<ActivityDraft>(draft);

  if (submittedId) {
    return (
      <div className="panel">
        <div className="panel-header">
          <h2>Activity submitted</h2>
        </div>
        <div className="panel-body">
          <div className="notice success">
            Activity #{submittedId} written to the ERP and ticket marked DONE.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="panel" style={{ borderColor: "var(--accent)" }}>
      <div className="panel-header">
        <h2>Review activity before submitting</h2>
      </div>
      <div className="panel-body">
        {FIELDS.map((f) => (
          <div className="field" key={f.key}>
            <label>{f.label}</label>
            <textarea
              rows={f.rows}
              value={fields[f.key] || ""}
              onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })}
            />
          </div>
        ))}
        <button className="primary" onClick={() => onSubmit(fields)}>
          Submit activity to ERP
        </button>
      </div>
    </div>
  );
}
