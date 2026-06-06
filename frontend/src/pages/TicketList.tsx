import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Ticket, TicketStatus } from "../api/client";

const SORTS = [
  { value: "date", label: "Date" },
  { value: "priority", label: "Priority" },
  { value: "status", label: "Status" },
];

function StatusBadge({ status }: { status: TicketStatus }) {
  return <span className={`badge ${status.toLowerCase()}`}>{status}</span>;
}

export default function TicketList() {
  const navigate = useNavigate();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [sort, setSort] = useState("date");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .listTickets({ sort, status: status || undefined, priority: priority || undefined })
      .then((t) => setTickets(t))
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [sort, status, priority]);

  return (
    <div>
      <h1>My Open Tickets</h1>
      <div className="toolbar">
        <div>
          <label>Sort by</label>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>Status</label>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="OPEN">Open</option>
            <option value="PENDING">Pending</option>
            <option value="DONE">Done</option>
          </select>
        </div>
        <div>
          <label>Priority</label>
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">All</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
      </div>

      {error && <div className="notice error">Could not load tickets — {error}</div>}
      {loading && <div className="notice">Loading tickets…</div>}
      {!loading && !error && tickets.length === 0 && (
        <div className="notice">No tickets match the current filters.</div>
      )}

      {tickets.length > 0 && (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Title</th>
                <th>Customer</th>
                <th>Priority</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr key={t.id} onClick={() => navigate(`/tickets/${t.id}`)}>
                  <td className="mono">#{t.id}</td>
                  <td>{t.title}</td>
                  <td>{t.customer_name}</td>
                  <td className={`prio-${t.priority}`}>{t.priority}</td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
