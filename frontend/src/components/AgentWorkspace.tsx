import React, { useCallback, useEffect, useRef, useState } from "react";
import { subscribe } from "../api";
import type {
  SSEEvent,
  PhaseChangeData,
  ThoughtData,
  HypothesesData,
  HypothesisItem,
  PlanData,
  AwaitingApprovalData,
  CommandResultData,
  SafetyBlockData,
  ValidationData,
  PersistCheckData,
  ActivityDraftData,
  ErrorData,
  LogEntry,
  Activity,
} from "../types";
import HypothesisPanel from "./HypothesisPanel";
import CommandApproval from "./CommandApproval";
import ActivityReview from "./ActivityReview";
import Controls from "./Controls";

interface Props {
  ticketId: number;
  sessionId: string;
  onBack: () => void;
  onDone: () => void;
}

function classificationClass(cls: string): string {
  switch (cls) {
    case "read_only": return "badge badge-read_only";
    case "needs_approval": return "badge badge-needs_approval";
    case "hard_block": return "badge badge-hard_block";
    default: return "badge";
  }
}

export default function AgentWorkspace({ ticketId, sessionId, onBack, onDone }: Props) {
  const [phase, setPhase] = useState<string>("starting");
  const [hypotheses, setHypotheses] = useState<HypothesisItem[]>([]);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [pendingApproval, setPendingApproval] = useState<AwaitingApprovalData | null>(null);
  const [activityDraft, setActivityDraft] = useState<Activity | null>(null);
  const [safetyBlock, setSafetyBlock] = useState<SafetyBlockData | null>(null);
  const [errorBanners, setErrorBanners] = useState<ErrorData[]>([]);
  const [validationResult, setValidationResult] = useState<ValidationData | null>(null);
  const [persistCheckResult, setPersistCheckResult] = useState<PersistCheckData | null>(null);
  const [sessionDone, setSessionDone] = useState(false);
  const [sseError, setSseError] = useState<string | null>(null);

  const logEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  const appendLog = useCallback((entry: LogEntry) => {
    setLog((prev) => [...prev, entry]);
  }, []);

  useEffect(() => {
    const es = subscribe(
      sessionId,
      (ev: SSEEvent) => {
        const ts = ev.ts ?? new Date().toISOString();
        switch (ev.type) {
          case "phase_change": {
            const d = ev.data as PhaseChangeData;
            setPhase(d.phase);
            break;
          }
          case "thought": {
            const d = ev.data as ThoughtData;
            appendLog({ kind: "thought", data: d, ts });
            break;
          }
          case "hypotheses": {
            const d = ev.data as HypothesesData;
            setHypotheses(d.items ?? []);
            break;
          }
          case "plan": {
            const d = ev.data as PlanData;
            appendLog({ kind: "plan", data: d, ts });
            break;
          }
          case "awaiting_approval": {
            const d = ev.data as AwaitingApprovalData;
            setPendingApproval(d);
            break;
          }
          case "command_result": {
            const d = ev.data as CommandResultData;
            appendLog({ kind: "command_result", data: d, ts });
            // Clear pending approval if this result matches
            setPendingApproval((prev) =>
              prev && prev.action_id === d.action_id ? null : prev
            );
            break;
          }
          case "safety_block": {
            const d = ev.data as SafetyBlockData;
            setSafetyBlock(d);
            appendLog({ kind: "safety_block", data: d, ts });
            break;
          }
          case "validation": {
            const d = ev.data as ValidationData;
            setValidationResult(d);
            appendLog({ kind: "validation", data: d, ts });
            break;
          }
          case "persist_check": {
            const d = ev.data as PersistCheckData;
            setPersistCheckResult(d);
            appendLog({ kind: "persist_check", data: d, ts });
            break;
          }
          case "activity_draft": {
            const d = ev.data as ActivityDraftData;
            setActivityDraft(d.activity);
            break;
          }
          case "done": {
            setSessionDone(true);
            setPhase("done");
            break;
          }
          case "error": {
            const d = ev.data as ErrorData;
            setErrorBanners((prev) => [...prev, d]);
            break;
          }
          default:
            break;
        }
      },
      () => {
        if (!sessionDone) {
          setSseError("Lost connection to agent stream. You can still approve/reject pending actions.");
        }
      }
    );
    esRef.current = es;

    return () => {
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  function dismissError(index: number) {
    setErrorBanners((prev) => prev.filter((_, i) => i !== index));
  }

  function dismissSafetyBlock() {
    setSafetyBlock(null);
  }

  return (
    <div className="workspace">
      {/* Header */}
      <div className="workspace-header">
        <div className="workspace-header-left">
          <button className="btn btn-secondary" onClick={onBack}>
            ← Back to ticket
          </button>
          <h2>Agent Workspace — Ticket #{ticketId}</h2>
        </div>
        <div className={`phase-indicator phase-${phase.toLowerCase().replace(/[^a-z]/g, "_")}`}>
          Phase: <strong>{phase.toUpperCase()}</strong>
        </div>
      </div>

      {/* Controls */}
      <Controls sessionId={sessionId} onAborted={onBack} />

      {/* SSE connection error */}
      {sseError && (
        <div className="error-banner">
          <strong>Connection:</strong> {sseError}
        </div>
      )}

      {/* Non-fatal error banners */}
      {errorBanners.map((err, i) => (
        <div key={i} className="error-banner dismissible">
          <strong>[{err.where}]</strong> {err.message}
          <button className="dismiss-btn" onClick={() => dismissError(i)}>✕</button>
        </div>
      ))}

      {/* Safety block callout */}
      {safetyBlock && (
        <div className="safety-block-callout">
          <div className="safety-block-header">
            <span className="badge badge-hard_block">HARD BLOCK</span>
            <span>Guardrail activated — command was NOT executed</span>
            <button className="dismiss-btn" onClick={dismissSafetyBlock}>✕</button>
          </div>
          <code className="safety-block-command">{safetyBlock.command}</code>
          <p className="safety-block-reason">{safetyBlock.reason}</p>
        </div>
      )}

      <div className="workspace-body">
        {/* Left column: hypotheses + validation */}
        <div className="workspace-left">
          <HypothesisPanel hypotheses={hypotheses} />

          {validationResult && (
            <div className={`panel result-panel ${validationResult.passed ? "result-pass" : "result-fail"}`}>
              <h4>Validation {validationResult.passed ? "✓ Passed" : "✗ Failed"}</h4>
              <p>{validationResult.detail}</p>
            </div>
          )}

          {persistCheckResult && (
            <div className={`panel result-panel ${persistCheckResult.passed ? "result-pass" : "result-fail"}`}>
              <h4>Persist Check {persistCheckResult.passed ? "✓ Passed" : "✗ Failed"}</h4>
              <p>{persistCheckResult.detail}</p>
            </div>
          )}
        </div>

        {/* Right column: approval + activity + log */}
        <div className="workspace-right">
          {/* Pending approval */}
          {pendingApproval && !sessionDone && (
            <CommandApproval
              sessionId={sessionId}
              pending={pendingApproval}
              onDone={() => setPendingApproval(null)}
            />
          )}

          {/* Activity review */}
          {activityDraft && (
            <ActivityReview
              sessionId={sessionId}
              draft={activityDraft}
              onSubmitted={onDone}
            />
          )}

          {/* Command log */}
          <div className="panel log-panel">
            <h4>Command Log</h4>
            {log.length === 0 ? (
              <p className="state-info">Waiting for agent activity…</p>
            ) : (
              <div className="log-scroll">
                {log.map((entry, i) => (
                  <LogEntryRow key={i} entry={entry} />
                ))}
                <div ref={logEndRef} />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Session done banner */}
      {sessionDone && (
        <div className="success-banner">
          Session complete. Ticket has been set to DONE.
          <button className="btn btn-primary" style={{ marginLeft: 16 }} onClick={onDone}>
            Back to ticket list
          </button>
        </div>
      )}
    </div>
  );
}

function LogEntryRow({ entry }: { entry: LogEntry }) {
  switch (entry.kind) {
    case "thought":
      return (
        <div className="log-entry log-thought">
          <span className="log-ts">{formatTs(entry.ts)}</span>
          <span className="log-label">Thought</span>
          <span className="log-text">{entry.data.text}</span>
        </div>
      );
    case "plan":
      return (
        <div className="log-entry log-plan">
          <span className="log-ts">{formatTs(entry.ts)}</span>
          <span className="log-label">Plan ({entry.data.commands.length} commands)</span>
          {entry.data.commands.map((cmd, i) => (
            <div key={i} className="log-plan-cmd">
              <code>{cmd.command}</code>
              <span className={`badge badge-${cmd.classification}`}>{cmd.classification}</span>
              <span className="log-purpose">{cmd.purpose}</span>
            </div>
          ))}
        </div>
      );
    case "command_result":
      return (
        <div className="log-entry log-cmd-result">
          <span className="log-ts">{formatTs(entry.ts)}</span>
          <span className={`badge badge-${entry.data.classification}`}>{entry.data.classification}</span>
          <code className="log-command">{entry.data.command}</code>
          <span className={`exit-code ${entry.data.exit_code === 0 ? "exit-ok" : "exit-err"}`}>
            exit {entry.data.exit_code}
          </span>
          <span className="log-approval">[{entry.data.approval}]</span>
          {entry.data.output_summary && (
            <pre className="log-output">{entry.data.output_summary}</pre>
          )}
        </div>
      );
    case "safety_block":
      return (
        <div className="log-entry log-safety">
          <span className="log-ts">{formatTs(entry.ts)}</span>
          <span className="badge badge-hard_block">BLOCKED</span>
          <code className="log-command">{entry.data.command}</code>
          <span className="log-reason">{entry.data.reason}</span>
        </div>
      );
    case "validation":
      return (
        <div className={`log-entry ${entry.data.passed ? "log-pass" : "log-fail"}`}>
          <span className="log-ts">{formatTs(entry.ts)}</span>
          <span className="log-label">Validation: {entry.data.passed ? "PASS" : "FAIL"}</span>
          <span className="log-text">{entry.data.detail}</span>
        </div>
      );
    case "persist_check":
      return (
        <div className={`log-entry ${entry.data.passed ? "log-pass" : "log-fail"}`}>
          <span className="log-ts">{formatTs(entry.ts)}</span>
          <span className="log-label">Persist Check: {entry.data.passed ? "PASS" : "FAIL"}</span>
          <span className="log-text">{entry.data.detail}</span>
        </div>
      );
    default:
      return null;
  }
}

function formatTs(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}
