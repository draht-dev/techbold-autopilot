import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { api, CustomerSystem, Ticket } from "../api/client";

export default function TicketDetail() {
  const { id } = useParams();
  const ticketId = Number(id);
  const navigate = useNavigate();

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [system, setSystem] = useState<CustomerSystem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoApproveReads, setAutoApproveReads] = useState(true);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    setError(null);
    api.getTicket(ticketId).then(setTicket).catch((e) => setError(String(e.message || e)));
    api
      .getCustomerSystem(ticketId)
      .then(setSystem)
      .catch((e) => setError(String(e.message || e)));
  }, [ticketId]);

  async function start() {
    setStarting(true);
    try {
      const res = await api.startRun(ticketId, autoApproveReads);
      navigate(`/runs/${res.run_id}`);
    } catch (e: any) {
      setError(String(e.message || e));
      setStarting(false);
    }
  }

  if (error) return <div className="notice error">{error}</div>;
  if (!ticket) return <div className="notice">Loading ticket…</div>;

  return (
    <div>
      <button className="ghost" onClick={() => navigate("/")}>
        ← Back to tickets
      </button>
      <h1>
        #{ticket.id} · {ticket.title}
      </h1>

      <div className="row">
        <div className="col">
          <div className="panel">
            <div className="panel-header">
              <h2>Customer Report</h2>
              <span className={`badge ${ticket.status.toLowerCase()}`}>{ticket.status}</span>
            </div>
            <div className="panel-body">
              <dl className="kv">
                <dt>Customer</dt>
                <dd>{ticket.customer_name}</dd>
                <dt>Priority</dt>
                <dd className={`prio-${ticket.priority}`}>{ticket.priority}</dd>
                <dt>Tags</dt>
                <dd>{ticket.tags?.join(", ") || "—"}</dd>
              </dl>
              <div className="ticket-markdown" style={{ marginTop: 12 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{ticket.description}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>

        <div className="col">
          <div className="panel">
            <div className="panel-header">
              <h2>Customer System</h2>
            </div>
            <div className="panel-body">
              {system ? (
                <dl className="kv">
                  <dt>Host</dt>
                  <dd className="mono">
                    {system.system.ip}:{system.system.port}
                  </dd>
                  <dt>User</dt>
                  <dd className="mono">{system.system.username}</dd>
                  <dt>OS</dt>
                  <dd>{system.system.os}</dd>
                  <dt>Notes</dt>
                  <dd>{system.system.notes || "—"}</dd>
                </dl>
              ) : (
                <div className="muted">Loading system info…</div>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-body">
          <div className="field">
            <label>
              <input
                type="checkbox"
                checked={autoApproveReads}
                onChange={(e) => setAutoApproveReads(e.target.checked)}
                style={{ width: "auto", marginRight: 8 }}
              />
              Auto-approve safe read-only commands (mutations always require approval)
            </label>
          </div>
          <button className="primary" disabled={starting || !system} onClick={start}>
            {starting ? "Starting…" : "Start AI troubleshooting run"}
          </button>
          <p className="muted" style={{ marginTop: 8 }}>
            You will be asked to approve the SSH connection and every action that changes
            the system.
          </p>
        </div>
      </div>
    </div>
  );
}
