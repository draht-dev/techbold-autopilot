import React, { useCallback, useEffect, useState } from "react";
import { listTickets } from "../api";
import type { Ticket, TicketStatus } from "../types";

interface Props {
  onSelectTicket: (id: number) => void;
}

type SortKey = "date" | "priority" | "status" | "customer";

const PRIORITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function priorityClass(priority: string): string {
  switch (priority.toLowerCase()) {
    case "critical": return "badge badge-critical";
    case "high": return "badge badge-high";
    case "medium": return "badge badge-medium";
    case "low": return "badge badge-low";
    default: return "badge badge-medium";
  }
}

function statusClass(status: TicketStatus): string {
  switch (status) {
    case "OPEN": return "badge badge-status-open";
    case "PENDING": return "badge badge-status-pending";
    case "DONE": return "badge badge-status-done";
    default: return "badge";
  }
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return "—";
  try {
    return new Date(dateStr).toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

export default function TicketList({ onSelectTicket }: Props) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterPriority, setFilterPriority] = useState<string>("");
  const [filterCustomer, setFilterCustomer] = useState<string>("");
  const [sortKey, setSortKey] = useState<SortKey>("date");

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listTickets({ sort: "date" })
      .then((data) => {
        setTickets(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = tickets
    .filter((t) => {
      if (filterStatus && t.status !== filterStatus) return false;
      if (filterPriority && t.priority.toLowerCase() !== filterPriority.toLowerCase()) return false;
      if (filterCustomer && !t.customer_name.toLowerCase().includes(filterCustomer.toLowerCase())) return false;
      return true;
    })
    .sort((a, b) => {
      switch (sortKey) {
        case "date": {
          const da = a.created_at ? new Date(a.created_at).getTime() : 0;
          const db = b.created_at ? new Date(b.created_at).getTime() : 0;
          return db - da;
        }
        case "priority":
          return (PRIORITY_ORDER[a.priority.toLowerCase()] ?? 9) - (PRIORITY_ORDER[b.priority.toLowerCase()] ?? 9);
        case "status":
          return a.status.localeCompare(b.status);
        case "customer":
          return a.customer_name.localeCompare(b.customer_name);
        default:
          return 0;
      }
    });

  return (
    <div className="page">
      <h2>Tickets</h2>

      {/* Filter / sort bar */}
      <div className="filter-bar">
        <label>
          Status
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">All</option>
            <option value="OPEN">Open</option>
            <option value="PENDING">Pending</option>
            <option value="DONE">Done</option>
          </select>
        </label>

        <label>
          Priority
          <select value={filterPriority} onChange={(e) => setFilterPriority(e.target.value)}>
            <option value="">All</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>

        <label>
          Customer
          <input
            type="text"
            placeholder="Search…"
            value={filterCustomer}
            onChange={(e) => setFilterCustomer(e.target.value)}
          />
        </label>

        <label>
          Sort by
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
            <option value="date">Date (newest first)</option>
            <option value="priority">Priority</option>
            <option value="status">Status</option>
            <option value="customer">Customer</option>
          </select>
        </label>

        <button className="btn btn-secondary" onClick={load}>
          Refresh
        </button>
      </div>

      {/* Loading state */}
      {loading && <p className="state-info">Loading tickets…</p>}

      {/* Error state */}
      {error && !loading && (
        <div className="error-banner">
          <strong>Error loading tickets:</strong> {error}
          <button className="btn btn-secondary" onClick={load} style={{ marginLeft: 12 }}>
            Retry
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && filtered.length === 0 && (
        <div className="empty-state">
          {tickets.length === 0 ? "No tickets assigned to you." : "No tickets match the current filters."}
        </div>
      )}

      {/* Ticket table */}
      {!loading && !error && filtered.length > 0 && (
        <table className="ticket-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Customer</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((ticket) => (
              <tr
                key={ticket.id}
                className="ticket-row"
                onClick={() => onSelectTicket(ticket.id)}
              >
                <td className="ticket-title">{ticket.title}</td>
                <td>{ticket.customer_name}</td>
                <td>
                  <span className={priorityClass(ticket.priority)}>
                    {ticket.priority}
                  </span>
                </td>
                <td>
                  <span className={statusClass(ticket.status)}>
                    {ticket.status}
                  </span>
                </td>
                <td className="ticket-date">{formatDate(ticket.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
