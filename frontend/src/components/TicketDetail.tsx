import React, { useCallback, useEffect, useState } from "react";
import { getTicket, getCustomerSystem, createSession, startSession } from "../api";
import type { Ticket, CustomerSystem } from "../types";

interface Props {
  ticketId: number;
  onBack: () => void;
  onSessionStarted: (ticketId: number, sessionId: string) => void;
}

export default function TicketDetail({ ticketId, onBack, onSessionStarted }: Props) {
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [customerSystem, setCustomerSystem] = useState<CustomerSystem | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setLoadError(null);

    Promise.all([getTicket(ticketId), getCustomerSystem(ticketId)])
      .then(([t, cs]) => {
        setTicket(t);
        setCustomerSystem(cs);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setLoadError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, [ticketId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStartSession() {
    if (!ticket) return;
    setStarting(true);
    setStartError(null);
    try {
      const { session_id } = await createSession(ticket.id);
      await startSession(session_id);
      onSessionStarted(ticket.id, session_id);
    } catch (err: unknown) {
      setStartError(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div className="page">
        <button className="btn btn-secondary" onClick={onBack}>← Back to list</button>
        <p className="state-info">Loading ticket…</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="page">
        <button className="btn btn-secondary" onClick={onBack}>← Back to list</button>
        <div className="error-banner">
          <strong>Error loading ticket:</strong> {loadError}
          <button className="btn btn-secondary" onClick={load} style={{ marginLeft: 12 }}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!ticket) return null;

  const sys = customerSystem?.system;

  return (
    <div className="page">
      <button className="btn btn-secondary" onClick={onBack}>← Back to list</button>

      <h2 className="ticket-detail-title">{ticket.title}</h2>

      <div className="detail-meta">
        <span>Customer: <strong>{ticket.customer_name}</strong></span>
        <span>Priority: <span className={`badge badge-${ticket.priority.toLowerCase()}`}>{ticket.priority}</span></span>
        <span>Status: <span className={`badge badge-status-${ticket.status.toLowerCase()}`}>{ticket.status}</span></span>
        {ticket.sla_due_at && (
          <span>SLA Due: <strong>{new Date(ticket.sla_due_at).toLocaleString()}</strong></span>
        )}
      </div>

      <section className="detail-section">
        <h3>Customer Report</h3>
        <p className="ticket-description">{ticket.description}</p>
        {ticket.tags && ticket.tags.length > 0 && (
          <div className="tag-list">
            {ticket.tags.map((tag) => (
              <span key={tag} className="tag">{tag}</span>
            ))}
          </div>
        )}
      </section>

      <section className="detail-section">
        <h3>Customer System</h3>
        {sys ? (
          <table className="info-table">
            <tbody>
              <tr><th>IP Address</th><td><code>{sys.ip}</code></td></tr>
              <tr><th>Port</th><td><code>{sys.port}</code></td></tr>
              <tr><th>Username</th><td><code>{sys.username}</code></td></tr>
              <tr><th>OS</th><td>{sys.os}</td></tr>
              <tr>
                <th>Notes</th>
                <td className="notes-cell">{sys.notes || "—"}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="state-info">No system information available.</p>
        )}
      </section>

      {startError && (
        <div className="error-banner">
          <strong>Could not start session:</strong> {startError}
        </div>
      )}

      <div className="detail-actions">
        <button
          className="btn btn-primary"
          onClick={handleStartSession}
          disabled={starting || ticket.status === "DONE"}
        >
          {starting ? "Starting session…" : "Start session — Approve connection & begin diagnosis"}
        </button>
        {ticket.status === "DONE" && (
          <span className="state-info" style={{ marginLeft: 12 }}>
            This ticket is already DONE.
          </span>
        )}
      </div>
    </div>
  );
}
