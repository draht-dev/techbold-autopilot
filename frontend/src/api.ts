// Typed API calls to the backend (SPEC §8) + SSE subscription helper (SPEC §9)

import type {
  Ticket,
  CustomerSystem,
  Employee,
  Activity,
  SSEEvent,
} from "./types";

const API: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  "http://localhost:8000";

// ── helpers ────────────────────────────────────────────────────────────────

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string; message?: string };
      message = body.detail ?? body.message ?? message;
    } catch {
      // ignore json parse failure
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

// ── ERP-proxied endpoints ──────────────────────────────────────────────────

export function getMe(): Promise<Employee> {
  return request<Employee>("/api/me");
}

export interface TicketParams {
  status?: string;
  priority?: string;
  sort?: string;
  customer?: string;
}

export function listTickets(params: TicketParams = {}): Promise<Ticket[]> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.priority) qs.set("priority", params.priority);
  if (params.sort) qs.set("sort", params.sort);
  if (params.customer) qs.set("customer", params.customer);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return request<Ticket[]>(`/api/tickets${query}`);
}

export function getTicket(id: number): Promise<Ticket> {
  return request<Ticket>(`/api/tickets/${id}`);
}

export function getCustomerSystem(id: number): Promise<CustomerSystem> {
  return request<CustomerSystem>(`/api/tickets/${id}/customer-system`);
}

// ── Session endpoints ──────────────────────────────────────────────────────

export function createSession(ticket_id: number): Promise<{ session_id: string }> {
  return post<{ session_id: string }>("/api/agent/sessions", { ticket_id });
}

export function startSession(sid: string): Promise<unknown> {
  return post(`/api/agent/sessions/${sid}/start`);
}

export function approve(
  sid: string,
  action_id: string,
  edited_command?: string
): Promise<unknown> {
  return post(`/api/agent/sessions/${sid}/approve`, {
    action_id,
    ...(edited_command !== undefined ? { edited_command } : {}),
  });
}

export function reject(sid: string, action_id: string): Promise<unknown> {
  return post(`/api/agent/sessions/${sid}/reject`, { action_id });
}

export function retry(sid: string): Promise<unknown> {
  return post(`/api/agent/sessions/${sid}/retry`);
}

export function abort(sid: string): Promise<unknown> {
  return post(`/api/agent/sessions/${sid}/abort`);
}

export function submitActivity(
  sid: string,
  activity: Activity
): Promise<unknown> {
  return post(`/api/agent/sessions/${sid}/activity`, { activity });
}

export function devReset(): Promise<unknown> {
  return post("/api/dev/reset");
}

// ── SSE subscription ───────────────────────────────────────────────────────

const KNOWN_EVENT_TYPES = [
  "phase_change",
  "thought",
  "hypotheses",
  "plan",
  "awaiting_approval",
  "command_result",
  "safety_block",
  "validation",
  "persist_check",
  "activity_draft",
  "done",
  "error",
] as const;

export function subscribe(
  sid: string,
  onEvent: (ev: SSEEvent) => void,
  onError?: (err: Event) => void
): EventSource {
  const es = new EventSource(`${API}/api/agent/sessions/${sid}/stream`);

  const handler = (e: MessageEvent) => {
    try {
      const ev = JSON.parse(e.data as string) as SSEEvent;
      onEvent(ev);
    } catch {
      // malformed event — ignore
    }
  };

  // Named event listeners for each known type
  for (const type of KNOWN_EVENT_TYPES) {
    es.addEventListener(type, handler as EventListener);
  }

  // Fallback for unnamed messages
  es.onmessage = handler;

  if (onError) {
    es.onerror = onError;
  }

  return es;
}
