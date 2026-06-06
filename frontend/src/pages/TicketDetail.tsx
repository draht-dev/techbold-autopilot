import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, CustomerSystem, RunSnapshot, RunSummary, Ticket } from "../api/client";
import Markdown from "../components/Markdown";
import Resolution from "../components/Resolution";

export default function TicketDetail() {
  const { id } = useParams();
  const ticketId = Number(id);
  const navigate = useNavigate();

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [system, setSystem] = useState<CustomerSystem | null>(null);
  const [activeRun, setActiveRun] = useState<RunSummary | null>(null);
  const [resolution, setResolution] = useState<RunSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoApproveReads, setAutoApproveReads] = useState(true);
  const [starting, setStarting] = useState(false);

  function loadActiveRun() {
    // Surface an in-flight run for this ticket so it can be resumed instead of lost
    // (its SSH session lives on the backend, keyed by run_id — not in this tab).
    // The backend returns null once the run finishes, so this self-heals when polled.
    api.getActiveRun(ticketId).then(setActiveRun).catch(() => setActiveRun(null));
  }

  useEffect(() => {
    // Guard against stale responses: if ticketId changes (browser back/forward
    // between two ticket pages reuses this component instance), don't let an
    // in-flight fetch for the old ticket write into state.
    let cancelled = false;
    setError(null);
    api.getTicket(ticketId).then((t) => !cancelled && setTicket(t)).catch((e) => !cancelled && setError(String(e.message || e)));
    api
      .getCustomerSystem(ticketId)
      .then((s) => !cancelled && setSystem(s))
      .catch((e) => !cancelled && setError(String(e.message || e)));
    // The solution + full log of the run that resolved this ticket, if any.
    api
      .getResolution(ticketId)
      .then((r) => !cancelled && setResolution(r))
      .catch(() => !cancelled && setResolution(null));
    const load = () =>
      api.getActiveRun(ticketId).then((r) => !cancelled && setActiveRun(r)).catch(() => !cancelled && setActiveRun(null));
    load();
    // Poll so the banner doesn't go stale (e.g. the run finishes while open).
    const t = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketId]);

  // Only treat a run as resumable while it is genuinely in-flight.
  const resumable = !!activeRun && activeRun.active;
  // A resolved ticket is closed: the agent cannot be re-run on it (the backend
  // also enforces this), but the technician can still open a plain SSH session.
  const done = ticket?.status === "DONE";

  async function start() {
    setStarting(true);
    try {
      const res = await api.startRun(ticketId, autoApproveReads);
      window.open(`/runs/${res.run_id}`, "_blank", "noopener,noreferrer");
      loadActiveRun(); // the new run is now the resumable one
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setStarting(false);
    }
  }

  async function openShell() {
    setStarting(true);
    try {
      const res = await api.startShell(ticketId);
      window.open(`/runs/${res.run_id}`, "_blank", "noopener,noreferrer");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
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
              <div style={{ marginTop: 12 }}>
                <Markdown>{ticket.description}</Markdown>
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

      {resumable && activeRun && (
        <div className="notice warning" style={{ marginBottom: 12 }}>
          <strong>A run is already in progress for this ticket</strong> (phase{" "}
          {activeRun.phase}). Its SSH session is still live on the backend — resume it
          rather than starting over.{" "}
          <button
            className="ghost"
            style={{ marginLeft: 8 }}
            onClick={() => navigate(`/runs/${activeRun.id}`)}
          >
            Resume run →
          </button>
        </div>
      )}

      {resolution && <Resolution resolution={resolution} />}

      {done ? (
        // A resolved ticket: no agent re-run. Plain, agent-free SSH access stays
        // available so the technician can inspect the machine directly.
        <div className="panel">
          <div className="panel-body">
            <p className="muted" style={{ marginTop: 0 }}>
              This ticket is <strong>DONE</strong>. The AI troubleshooting agent cannot be
              re-run. You can still open a plain SSH session to inspect the machine — no
              agent runs, and you drive the terminal yourself.
            </p>
            <button className="primary" disabled={starting || !system} onClick={openShell}>
              {starting ? "Connecting…" : "Open SSH terminal (no agent)"}
            </button>
          </div>
        </div>
      ) : (
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
              {starting
                ? "Starting…"
                : resumable
                ? "Start a new run (replaces the current one)"
                : "Start AI troubleshooting run"}
            </button>
            <button
              className="ghost"
              style={{ marginLeft: 8 }}
              disabled={starting || !system}
              onClick={openShell}
            >
              Open SSH terminal (no agent)
            </button>
            <p className="muted" style={{ marginTop: 8 }}>
              {resumable
                ? "Starting a new run will stop the in-progress run above and close its SSH session."
                : "You will be asked to approve the SSH connection and every action that changes the system."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
