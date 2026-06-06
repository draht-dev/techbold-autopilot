// Mirror of backend pydantic models (SPEC §2, §9)

export type TicketStatus = "OPEN" | "PENDING" | "DONE";

export interface Ticket {
  id: number;
  title: string;
  description: string;
  priority: string;
  status: TicketStatus;
  customer_id: number;
  customer_name: string;
  tags?: string[];
  sla_due_at?: string | null;
  created_at?: string | null;
}

// Matches backend app/models.py Employee (GET /api/me response)
export interface Employee {
  id: number;
  firstname: string;
  lastname: string;
  username: string;
  teamname: string;
}

export interface Customer {
  id: number;
  name: string;
  email?: string;
  company?: string;
}

export interface SystemInfo {
  ip: string;
  port: number;
  username: string;
  os: string;
  notes: string;
}

export interface CustomerSystem {
  ticket_id: number;
  customer_id: number;
  system: SystemInfo;
}

export interface Activity {
  ticket_id: number;
  start_datetime: string;
  end_datetime: string;
  summary: string;
  root_cause: string;
  actions_taken: string;
  commands_summary: string;
  validation_result: string;
  description?: string;
}

// SSE event wrapper
export interface SSEEvent {
  type: string;
  session_id: string;
  ts: string;
  data: unknown;
}

// Narrow typed SSE data payloads (SPEC §9)
export interface PhaseChangeData {
  phase: string;
}

export interface ThoughtData {
  text: string;
}

export interface HypothesisItem {
  cause: string;
  evidence: string;
  confidence: number;
}

export interface HypothesesData {
  items: HypothesisItem[];
}

export interface PlanCommand {
  action_id: string;
  command: string;
  classification: string;
  mutating: boolean;
  purpose: string;
  expected_effect: string;
}

export interface PlanData {
  commands: PlanCommand[];
}

export interface AwaitingApprovalData {
  action_id: string;
  command: string;
  classification: string;
  purpose: string;
  expected_effect: string;
  rollback: string;
}

export interface CommandResultData {
  action_id: string;
  command: string;
  classification: string;
  approval: string;
  exit_code: number;
  output_summary: string;
}

export interface SafetyBlockData {
  command: string;
  reason: string;
}

export interface ValidationData {
  passed: boolean;
  detail: string;
}

export interface PersistCheckData {
  passed: boolean;
  detail: string;
}

export interface ActivityDraftData {
  activity: Activity;
}

export interface DoneData {
  ticket_id: number;
  status: "DONE";
}

export interface ErrorData {
  where: string;
  message: string;
}

// Log entry for command log in AgentWorkspace
export type LogEntry =
  | { kind: "plan"; data: PlanData; ts: string }
  | { kind: "command_result"; data: CommandResultData; ts: string }
  | { kind: "safety_block"; data: SafetyBlockData; ts: string }
  | { kind: "validation"; data: ValidationData; ts: string }
  | { kind: "persist_check"; data: PersistCheckData; ts: string }
  | { kind: "thought"; data: ThoughtData; ts: string };
