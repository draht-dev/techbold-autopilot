import { useState } from "react";
import { ActivityDraft, ManualReportOutcome } from "../api/client";
import Modal from "./Modal";

const FIELDS: { key: keyof ActivityDraft; label: string; rows: number }[] = [
  { key: "summary", label: "Summary (one sentence)", rows: 2 },
  { key: "root_cause", label: "Root cause (technical, not symptom)", rows: 2 },
  { key: "actions_taken", label: "Actions taken (in order)", rows: 4 },
  { key: "commands_summary", label: "Commands summary (no secrets)", rows: 3 },
  { key: "validation_result", label: "Validation result (concrete proof)", rows: 3 },
  { key: "description", label: "Description", rows: 3 },
];

interface Props {
  open: boolean;
  ticketTitle?: string;
  onClose: () => void;
  onSubmit: (fields: ActivityDraft, outcome: ManualReportOutcome) => Promise<void>;
}

export default function ManualReportModal({ open, ticketTitle, onClose, onSubmit }: Props) {
  const [fields, setFields] = useState<ActivityDraft>({
    summary: "",
    root_cause: "",
    actions_taken: "",
    commands_summary: "",
    validation_result: "",
    description: "",
  });
  const [outcome, setOutcome] = useState<ManualReportOutcome>("pending");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(fields, outcome);
      onClose();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title={ticketTitle ? `Report · ${ticketTitle}` : "Submit manual report"}
      open={open}
      onClose={onClose}
      width={640}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        File your findings directly to the ERP. The run will end and the ticket status will be
        updated based on the outcome you choose.
      </p>

      <div className="field">
        <label>Outcome</label>
        <select
          value={outcome}
          onChange={(e) => setOutcome(e.target.value as ManualReportOutcome)}
        >
          <option value="fixed">Fixed — mark ticket DONE</option>
          <option value="pending">Needs follow-up — mark ticket PENDING</option>
        </select>
      </div>

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

      {error && <div className="notice error">{error}</div>}

      <div className="btn-row" style={{ marginTop: 12 }}>
        <button className="primary" disabled={submitting} onClick={handleSubmit}>
          {submitting ? "Submitting…" : "Submit report to ERP"}
        </button>
        <button disabled={submitting} onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}
