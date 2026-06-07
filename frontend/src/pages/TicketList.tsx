import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, RunSummary, Ticket, TicketStatus } from "../api/client";

const SORTS = [
  { value: "date", label: "Date" },
  { value: "priority", label: "Priority" },
  { value: "status", label: "Status" },
];

function StatusBadge({ status }: { status: TicketStatus }) {
  return <span className={`badge ${status.toLowerCase()}`}>{status}</span>;
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 10 12" aria-hidden="true">
      <path d="M0 0l10 6-10 6z" />
    </svg>
  );
}

type DevNotice = { kind: "success" | "error"; text: string } | null;

/**
 * Developer-only actions for setting up a fresh test run: reset the team's ERP
 * state, and upload new SSH keys so runs work with freshly-issued credentials.
 */
function DevMenu() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<DevNotice>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Close the dropdown when clicking outside it.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const handleReset = async () => {
    setOpen(false);
    setBusy(true);
    setNotice(null);
    try {
      await api.resetMe();
      setNotice({ kind: "success", text: "Account reset — activities cleared, VMs rebooting." });
    } catch (e: any) {
      setNotice({ kind: "error", text: `Reset failed — ${String(e.message || e)}` });
    } finally {
      setBusy(false);
    }
  };

  const handleFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-selecting the same file later
    if (files.length === 0) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.uploadKeys(files);
      const names = res.saved.map((k) => k.name).join(", ");
      const where = res.s3 ? "saved locally + mirrored to S3" : "saved locally (S3 not configured)";
      setNotice({
        kind: "success",
        text: `Uploaded ${res.saved.length} key${res.saved.length === 1 ? "" : "s"} (${names}) — ${where}.`,
      });
    } catch (err: any) {
      setNotice({ kind: "error", text: `Key upload failed — ${String(err.message || err)}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dev-menu" ref={containerRef}>
      <button
        className="ghost"
        disabled={busy}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {busy ? "Working…" : "Dev ▾"}
      </button>
      {open && (
        <div className="dev-menu-dropdown" role="menu">
          <button role="menuitem" onClick={handleReset}>
            Reset account
          </button>
          <button role="menuitem" onClick={() => fileRef.current?.click()}>
            Upload keys
          </button>
        </div>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".pem,.key"
        multiple
        hidden
        onChange={handleFiles}
      />
      {notice && (
        <div className={`dev-menu-notice notice ${notice.kind}`} role="status">
          {notice.text}
        </div>
      )}
    </div>
  );
}

export default function TicketList() {
  const navigate = useNavigate();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [activeRuns, setActiveRuns] = useState<RunSummary[]>([]);
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

  useEffect(() => {
    // In-flight runs are discoverable here even if the run URL was lost on reload.
    // Polled so the list stays fresh while the dashboard is left open.
    const load = () =>
      api
        .listRuns()
        .then((rs) => setActiveRuns(rs.filter((r) => r.active)))
        .catch(() => setActiveRuns([]));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  // Map each ticket to its in-flight run (if any) so we can offer an inline
  // "resume" play button on the right of that ticket's row.
  const runByTicket = new Map(activeRuns.map((r) => [r.ticket_id, r]));

  return (
    <div>
      <div className="page-header">
        <h1>My Open Tickets</h1>
        <DevMenu />
      </div>

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
                <th className="col-action" />
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => {
                const run = runByTicket.get(t.id);
                return (
                  <tr key={t.id} onClick={() => navigate(`/tickets/${t.id}`)}>
                    <td className="mono">#{t.id}</td>
                    <td>{t.title}</td>
                    <td>{t.customer_name}</td>
                    <td className={`prio-${t.priority}`}>{t.priority}</td>
                    <td>
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="col-action">
                      {run ? (
                        <button
                          className="play-btn live"
                          title={`Resume run — live ${run.phase} session`}
                          aria-label={`Resume run for ticket ${t.id}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/runs/${run.id}`);
                          }}
                        >
                          <PlayIcon />
                        </button>
                      ) : (
                        <span className="col-action-placeholder" aria-hidden="true" />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
