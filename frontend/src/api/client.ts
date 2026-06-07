// Typed client for the team backend (REST + WebSocket).

export const API_BASE: string =
  (import.meta as any).env?.VITE_API_BASE || "http://localhost:8000";

export type TicketStatus = "OPEN" | "PENDING" | "DONE";

export interface Ticket {
  id: number;
  title: string;
  description: string;
  priority: string;
  status: TicketStatus;
  customer_id: number;
  customer_name: string;
  tags: string[];
  sla_due_at?: string | null;
  created_at?: string | null;
}

export interface SystemInfo {
  ip: string;
  port: number;
  username: string;
  os: string;
  notes?: string | null;
}

export interface CustomerSystem {
  ticket_id: number;
  customer_id: number;
  system: SystemInfo;
}

export interface Employee {
  id: number;
  firstname: string;
  lastname: string;
  username: string;
  teamname: string;
}

export interface HypothesisComment {
  author: string;
  text: string;
  ts: string;
}

export interface Hypothesis {
  id: string;
  rank: number;
  title: string;
  reasoning: string;
  evidence: string;
  /** One or more read-only check commands (a hypothesis is not limited to one). */
  checks: string[];
  /** Relative likelihood as a percentage (0-100), normalised across the set. */
  likelihood?: number | null;
  source: "agent" | "technician";
  comments: HypothesisComment[];
  status: "open" | "checking" | "confirmed" | "rejected";
}

export interface ActivityDraft {
  summary: string;
  root_cause: string;
  actions_taken: string;
  commands_summary: string;
  validation_result: string;
  description: string;
}

export type ManualReportOutcome = "fixed" | "pending";

export interface ManualReportResult {
  activity_id: number | null;
  status: TicketStatus;
  outcome: ManualReportOutcome;
}

export interface RunEvent {
  type: string;
  ts: string;
  [key: string]: any;
}

export interface AgentToolCall {
  name: string;
  args: Record<string, any>;
}

export interface AgentMessage {
  ts: string;
  /** "assistant" = the model's thinking/tool intentions; "tool_result" = output. */
  kind: "assistant" | "tool_result";
  text: string;
  reasoning?: string;
  tool_calls?: AgentToolCall[];
  name?: string;
  context_tokens?: number;
  compactions?: number;
}

export interface AgentDecision {
  id: string;
  question: string;
  options: string[];
  context?: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface RunSnapshot {
  id: string;
  ticket_id: number;
  phase: string;
  /** "agent" = autonomous run; "shell" = a plain SSH session with no agent. */
  kind?: string;
  auto_approve_reads: boolean;
  error?: string | null;
  /** Final outcome of a resolving run (e.g. "fixed"), once it submitted activity. */
  outcome?: string | null;
  /** The submitted solution (how the ticket was fixed), or null if not resolved. */
  submitted_activity?: ActivityDraft | null;
  events: RunEvent[];
}

export interface RunSummary {
  id: string;
  ticket_id: number;
  phase: string;
  active: boolean;
  started_at?: string | null;
  error?: string | null;
}

/** Phases in which a run is over: SSH released, no longer resumable. */
export const FINAL_PHASES = ["DONE", "STOPPED", "ERROR"];

export interface KeyUploadResult {
  saved: { name: string; bytes: number; s3: boolean }[];
  keys_dir: string;
  s3: boolean;
}

/** Multipart upload (the JSON `req` helper can't carry a file body). */
async function uploadFiles<T>(path: string, files: File[]): Promise<T> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export const api = {
  getMe: () => req<Employee>("/api/me"),
  /** Dev: reset the team's ERP state (clears activities, reboots VMs). */
  resetMe: () => req<Record<string, unknown>>("/api/me/reset", { method: "POST" }),
  /** Dev: upload fresh SSH keys so runs work with newly-issued keys. */
  uploadKeys: (files: File[]) => uploadFiles<KeyUploadResult>("/api/dev/keys", files),
  voiceConfig: () => req<{ enabled: boolean; agent_id: string }>("/api/voice/config"),
  voiceSignedUrl: () => req<{ signed_url: string }>("/api/voice/signed-url"),
  getRun: (runId: string) => req<RunSnapshot>(`/api/runs/${runId}`),
  listRuns: () => req<RunSummary[]>("/api/runs"),
  /** The in-flight run for a ticket (resume target), or null. Lightweight summary. */
  getActiveRun: (ticketId: number) =>
    req<RunSummary | null>(`/api/tickets/${ticketId}/active-run`),
  listTickets: (params: { status?: string; priority?: string; sort?: string }) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.priority) q.set("priority", params.priority);
    if (params.sort) q.set("sort", params.sort);
    return req<Ticket[]>(`/api/tickets?${q.toString()}`);
  },
  getTicket: (id: number) => req<Ticket>(`/api/tickets/${id}`),
  getCustomerSystem: (id: number) =>
    req<CustomerSystem>(`/api/tickets/${id}/customer-system`),
  /** The finished run that resolved a ticket (solution + full log), or null. */
  getResolution: (ticketId: number) =>
    req<RunSnapshot | null>(`/api/tickets/${ticketId}/resolution`),
  startRun: (ticketId: number, autoApproveReads?: boolean) =>
    req<{ run_id: string; phase: string; auto_approve_reads: boolean }>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId, auto_approve_reads: autoApproveReads }),
    }),
  /** Open a plain SSH session (no agent) — allowed even on a DONE ticket. */
  startShell: (ticketId: number) =>
    req<{ run_id: string; phase: string; kind: string }>("/api/runs/shell", {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId }),
    }),
  stopRun: (runId: string) =>
    req<{ ok: boolean }>(`/api/runs/${runId}/stop`, { method: "POST" }),
  setTicketStatus: (ticketId: number, status: TicketStatus) =>
    req<Ticket>(`/api/tickets/${ticketId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  submitManualReport: (
    runId: string,
    activity: ActivityDraft,
    outcome: ManualReportOutcome
  ) =>
    req<ManualReportResult>(`/api/runs/${runId}/manual-report`, {
      method: "POST",
      body: JSON.stringify({ ...activity, outcome }),
    }),
};

export function runSocketUrl(runId: string): string {
  const wsBase = API_BASE.replace(/^http/, "ws");
  return `${wsBase}/ws/runs/${runId}`;
}
