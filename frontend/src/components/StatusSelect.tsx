import { TicketStatus } from "../api/client";

const STATUSES: TicketStatus[] = ["OPEN", "PENDING", "DONE"];

interface Props {
  status: TicketStatus;
  onChange: (status: TicketStatus) => void;
  disabled?: boolean;
}

export default function StatusSelect({ status, onChange, disabled }: Props) {
  return (
    <select
      className={`status-select badge ${status.toLowerCase()}`}
      value={status}
      disabled={disabled}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onChange(e.target.value as TicketStatus)}
      aria-label="Ticket status"
    >
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {s}
        </option>
      ))}
    </select>
  );
}
