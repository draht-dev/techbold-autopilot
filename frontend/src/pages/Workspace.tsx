import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ActivityDraft,
  Hypothesis,
  RunEvent,
  Ticket,
  api,
  runSocketUrl,
} from "../api/client";
import TerminalView from "../components/Terminal";
import HypothesisList from "../components/HypothesisList";
import ApprovalPrompt, { Approval } from "../components/ApprovalPrompt";
import RunControls from "../components/RunControls";
import ActivityReview from "../components/ActivityReview";

const BUSY_PHASES = [
  "CONNECTING",
  "RECON",
  "HYPOTHESES",
  "CHECK",
  "FIX_PROPOSE",
  "APPLY",
  "VALIDATE",
  "PERSIST_VERIFY",
  "SUBMITTING",
];
const FINAL_PHASES = ["DONE", "STOPPED", "ERROR"];
const ACTIVE_HYPOTHESIS_PHASES = [
  "CHECK",
  "FIX_PROPOSE",
  "APPLY",
  "VALIDATE",
  "PERSIST_VERIFY",
];

export default function Workspace() {
  const { runId } = useParams();
  const wsRef = useRef<WebSocket | null>(null);

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [phase, setPhase] = useState("CONNECTING");
  const [autoApproveReads, setAutoApproveReads] = useState(true);
  const [termChunks, setTermChunks] = useState<string[]>([]);
  const [logEvents, setLogEvents] = useState<RunEvent[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [awaitingSelection, setAwaitingSelection] = useState(false);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [activityDraft, setActivityDraft] = useState<ActivityDraft | null>(null);
  const [submittedId, setSubmittedId] = useState<number | null>(null);
  const [connError, setConnError] = useState<string | null>(null);

  function send(message: object) {
    wsRef.current?.send(JSON.stringify(message));
  }

  useEffect(() => {
    if (!runId) return;
    let closed = false;

    api
      .getRun(runId)
      .then((snap) => {
        setAutoApproveReads(snap.auto_approve_reads);
        return api.getTicket(snap.ticket_id);
      })
      .then(setTicket)
      .catch(() => {
        /* ticket display is best-effort */
      });

    const ws = new WebSocket(runSocketUrl(runId));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      let ev: RunEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      handleEvent(ev);
    };
    ws.onerror = () => {
      if (!closed) setConnError("WebSocket connection error");
    };

    return () => {
      closed = true;
      ws.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  function handleEvent(ev: RunEvent) {
    switch (ev.type) {
      case "term.data":
        setTermChunks((prev) => [...prev, ev.data]);
        break;
      case "run.state":
        setPhase(ev.phase);
        break;
      case "hypotheses":
        setHypotheses(ev.items || []);
        setAwaitingSelection(
          (ev.items || []).some((h: Hypothesis) => h.status === "open")
        );
        break;
      case "approval.request":
        setApproval({ id: ev.id, kind: ev.kind, payload: ev.payload, purpose: ev.purpose });
        break;
      case "approval.resolved":
        setApproval((prev) => (prev && prev.id === ev.id ? null : prev));
        break;
      case "activity.draft":
        setActivityDraft(ev.draft);
        break;
      case "activity.submitted":
        setSubmittedId(ev.activity_id ?? -1);
        break;
      case "validation.result":
      case "info":
      case "error":
        setLogEvents((prev) => [...prev, ev]);
        break;
      default:
        break;
    }
  }

  function selectHypothesis(id: string, comment?: string) {
    send({ type: "select_hypothesis", id, comment });
    setAwaitingSelection(false);
    setHypotheses((prev) =>
      prev.map((h) =>
        h.id === id ? { ...h, status: "checking", comment: comment ?? h.comment } : h
      )
    );
  }

  function submitCustomHypothesis(title: string, comment?: string) {
    send({ type: "submit_custom_hypothesis", title, comment });
    setAwaitingSelection(false);
    setHypotheses([
      {
        id: "custom",
        rank: 0,
        title,
        reasoning: "Proposed by the technician.",
        evidence: "",
        proposed_check: "",
        status: "checking",
        source: "technician",
        comment: comment ?? null,
      },
    ]);
  }

  function annotateHypothesis(id: string, comment: string) {
    send({ type: "hypothesis.comment", id, comment });
    setHypotheses((prev) =>
      prev.map((h) => (h.id === id ? { ...h, comment: comment || null } : h))
    );
  }

  function decideApproval(approved: boolean, edited?: string) {
    if (!approval) return;
    send({ type: "approval.decision", id: approval.id, approved, edited });
    setApproval(null);
  }

  function toggleReads(value: boolean) {
    setAutoApproveReads(value);
    send({ type: "mode.set", auto_approve_reads: value });
  }

  function submitActivity(edited: ActivityDraft) {
    send({ type: "submit_activity", activity: edited });
  }

  const busy = BUSY_PHASES.includes(phase);
  const terminalEnabled = !!approval || !busy;
  const runActive = !FINAL_PHASES.includes(phase);

  const visibleHypotheses = (() => {
    if (phase === "HYPOTHESES" && awaitingSelection) return hypotheses;
    if (ACTIVE_HYPOTHESIS_PHASES.includes(phase)) {
      const active = hypotheses.find(
        (h) => h.status === "checking" || h.status === "confirmed"
      );
      return active ? [active] : [];
    }
    return [];
  })();
  const showHypothesisPanel = visibleHypotheses.length > 0;

  return (
    <div>
      <Link className="ghost" to={ticket ? `/tickets/${ticket.id}` : "/"}>
        ← Back to ticket
      </Link>
      <h1>
        Troubleshooting {ticket ? `#${ticket.id} · ${ticket.title}` : `run ${runId}`}
      </h1>

      {connError && <div className="notice error">{connError}</div>}

      <RunControls
        phase={phase}
        autoApproveReads={autoApproveReads}
        active={runActive}
        onToggleReads={toggleReads}
        onStop={() => send({ type: "stop" })}
      />

      <div className="workspace-grid">
        <div>
          <TerminalView
            chunks={termChunks}
            enabled={terminalEnabled}
            onCommand={(cmd) => send({ type: "terminal.input", command: cmd })}
          />
          <div className="panel">
            <div className="panel-header">
              <h2>Agent Activity</h2>
            </div>
            <div className="panel-body" style={{ maxHeight: 240, overflow: "auto" }}>
              {logEvents.length === 0 && <div className="muted">No activity yet.</div>}
              {logEvents.map((ev, i) => (
                <LogLine key={i} ev={ev} />
              ))}
            </div>
          </div>
        </div>

        <div>
          {approval && <ApprovalPrompt approval={approval} onDecide={decideApproval} />}
          {showHypothesisPanel && (
            <HypothesisList
              hypotheses={visibleHypotheses}
              selectable={awaitingSelection && !approval}
              compact={!awaitingSelection}
              onSelect={selectHypothesis}
              onSubmitCustom={submitCustomHypothesis}
              onComment={annotateHypothesis}
            />
          )}
          {activityDraft && (
            <ActivityReview
              draft={activityDraft}
              submittedId={submittedId}
              onSubmit={submitActivity}
            />
          )}
          {!approval && !awaitingSelection && !activityDraft && !showHypothesisPanel && (
            <div className="notice">{phaseHint(phase)}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function LogLine({ ev }: { ev: RunEvent }) {
  if (ev.type === "error") {
    return <div className="notice error">{ev.message}</div>;
  }
  if (ev.type === "validation.result") {
    return (
      <div className={`notice ${ev.success ? "success" : "warning"}`}>
        Validation {ev.after_restart ? "(after restart) " : ""}
        {ev.success ? "passed" : "failed"}: {ev.proof}
      </div>
    );
  }
  return <div style={{ padding: "2px 0" }}>{ev.text}</div>;
}

function phaseHint(phase: string): string {
  switch (phase) {
    case "CONNECTING":
      return "Waiting to connect to the customer VM…";
    case "RECON":
      return "Agent is gathering read-only diagnostics…";
    case "HYPOTHESES":
      return "Review ranked hypotheses and select one to check, or propose your own.";
    case "CHECK":
      return "Agent is checking the selected hypothesis…";
    case "FIX_PROPOSE":
      return "Review and approve the proposed fix…";
    case "APPLY":
      return "Agent is applying the approved fix…";
    case "VALIDATE":
    case "PERSIST_VERIFY":
      return "Agent is validating the fix and verifying persistence…";
    case "DONE":
      return "Run complete. Activity submitted and ticket marked DONE.";
    case "STOPPED":
      return "Run stopped by technician.";
    case "ERROR":
      return "The run ended with an error — see the activity log.";
    default:
      return "Working…";
  }
}
