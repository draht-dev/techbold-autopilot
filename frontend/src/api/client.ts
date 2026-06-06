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

export interface Hypothesis {
  id: string;
  rank: number;
  title: string;
  reasoning: string;
  evidence: string;
  proposed_check: string;
  likelihood?: number | null;
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

export interface RunEvent {
  type: string;
  ts: string;
  [key: string]: any;
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
  auto_approve_reads: boolean;
  error?: string | null;
  events: RunEvent[];
}

export const api = {
  getMe: () => req<Employee>("/api/me"),
  getRun: (runId: string) => req<RunSnapshot>(`/api/runs/${runId}`),
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
  startRun: (ticketId: number, autoApproveReads?: boolean) =>
    req<{ run_id: string; phase: string; auto_approve_reads: boolean }>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId, auto_approve_reads: autoApproveReads }),
    }),
  stopRun: (runId: string) =>
    req<{ ok: boolean }>(`/api/runs/${runId}/stop`, { method: "POST" }),
};

export function runSocketUrl(runId: string): string {
  const wsBase = API_BASE.replace(/^http/, "ws");
  return `${wsBase}/ws/runs/${runId}`;
}
