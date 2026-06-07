import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ActivityDraft,
  AgentDecision,
  FINAL_PHASES,
  Hypothesis,
  ManualReportOutcome,
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
import DecisionPrompt from "../components/DecisionPrompt";
import ManualReportModal from "../components/ManualReportModal";
import VoiceControl from "../components/VoiceControl";
import { useVoiceControl, VoiceController } from "../voice/useVoiceControl";

const BUSY_PHASES = [
  "CONNECTING",
  "RECON",
  "REPRODUCING",
  "INVESTIGATING",
  "CHECK",
  "APPLY",
  "VALIDATE",
  "PERSIST_VERIFY",
  "SUBMITTING",
];

export default function Workspace() {
  const { runId } = useParams();
  const wsRef = useRef<WebSocket | null>(null);
  const termSize = useRef({ cols: 120, rows: 30 });

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [phase, setPhase] = useState("CONNECTING");
  const [autoApproveReads, setAutoApproveReads] = useState(true);
  const [termChunks, setTermChunks] = useState<string[]>([]);
  // One chronological stream of everything the agent does that's worth showing the
  // technician: its thoughts, the commands it runs (gray, no output), and run
  // milestones/errors. Raw tool-result output stays in the Terminal above.
  const [activity, setActivity] = useState<RunEvent[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [decision, setDecision] = useState<AgentDecision | null>(null);
  const [activityDraft, setActivityDraft] = useState<ActivityDraft | null>(null);
  const [submittedId, setSubmittedId] = useState<number | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);

  // controllerRef is updated every render so voice-tool callbacks always close
  // over the latest state without triggering hook dependency loops.
  const controllerRef = useRef<VoiceController>({} as VoiceController);
  const voice = useVoiceControl(controllerRef);
  // Stable ref to voice.notify so WS event handler never captures a stale copy.
  const notifyRef = useRef(voice.notify);
  notifyRef.current = voice.notify;

  function send(message: object) {
    wsRef.current?.send(JSON.stringify(message));
  }

  useEffect(() => {
    if (!runId) return;
    let closed = false;

    api
      .getRun(runId)
      .then((snap) => {
        // Apply the authoritative run state from the snapshot immediately, so a
        // reload/back-then-return reflects the real phase before WS replay arrives
        // (rather than flashing the default "CONNECTING").
        setPhase(snap.phase);
        setAutoApproveReads(snap.auto_approve_reads);
        if (snap.error) setConnError(snap.error);
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

  useEffect(() => {
    api.voiceConfig().then((c) => setVoiceEnabled(c.enabled)).catch(() => setVoiceEnabled(false));
  }, []);

  function handleEvent(ev: RunEvent) {
    switch (ev.type) {
      case "term.data":
        setTermChunks((prev) => [...prev, ev.data]);
        break;
      case "run.state":
        setPhase(ev.phase);
        // Surface phase transitions as dividers in the activity stream, but skip
        // repeats (set_phase re-emits the same phase in some flows).
        setActivity((prev) => {
          const lastPhase = [...prev].reverse().find((e) => e.type === "run.state")?.phase;
          if (lastPhase === ev.phase) return prev;
          // Proactively tell the voice agent about phase changes so it can
          // narrate milestones without the technician asking.
          notifyRef.current(`Phase changed to ${ev.phase}.`);
          return [...prev, ev];
        });
        break;
      case "hypotheses": {
        const items: Hypothesis[] = ev.items || [];
        setHypotheses(items);
        if (items.length > 0) {
          const top = items
            .slice(0, 3)
            .map((h: Hypothesis) => `#${h.rank} ${h.title}`)
            .join("; ");
          notifyRef.current(
            `${items.length} ranked hypothesis${items.length > 1 ? "es are" : " is"} ready to choose from: ${top}. You need to pick one.`
          );
        }
        break;
      }
      case "approval.request": {
        const a = { id: ev.id, kind: ev.kind, payload: ev.payload, purpose: ev.purpose };
        setApproval(a);
        // Notify voice so it can proactively ask the technician for approval.
        const cmd = ev.payload?.command ?? (ev.payload?.commands || []).join("; ") ?? "";
        const isMutation = ev.kind === "command" || ev.kind === "fix";
        notifyRef.current(
          `Approval required (${ev.kind}): the agent wants to run: ${cmd || ev.purpose || "(see UI)"}. ${isMutation ? "This is a mutation; please confirm before approving." : ""}`
        );
        break;
      }
      case "approval.resolved":
        setApproval((prev) => (prev && prev.id === ev.id ? null : prev));
        break;
      case "decision.request":
        setDecision({
          id: ev.id,
          question: ev.question,
          options: ev.options || [],
          context: ev.context,
        });
        notifyRef.current(
          `Decision needed: ${ev.question} Options are: ${(ev.options || []).join(", ")}.`
        );
        break;
      case "decision.resolved":
        setDecision((prev) => (prev && prev.id === ev.id ? null : prev));
        break;
      case "activity.draft":
        setActivityDraft(ev.draft);
        notifyRef.current("An activity draft is ready to review and submit. Say 'submit activity' when ready.");
        break;
      case "activity.submitted":
        setSubmittedId(ev.activity_id ?? -1);
        break;
      case "agent.message":
      case "command.run":
      case "validation.result":
      case "info":
      case "error":
        setActivity((prev) => [...prev, ev]);
        break;
      default:
        break;
    }
  }

  function selectHypothesis(id: string) {
    send({ type: "select_hypothesis", id });
    setHypotheses((prev) =>
      prev.map((h) => (h.id === id ? { ...h, status: "checking" } : h))
    );
  }

  function commentHypothesis(id: string, text: string) {
    send({ type: "comment_hypothesis", id, text });
  }

  function submitOwnHypothesis(h: { title: string; reasoning: string; checks: string[] }) {
    send({ type: "submit_hypothesis", hypothesis: h });
  }

  function decideApproval(approved: boolean, edited?: string, reason?: string) {
    if (!approval) return;
    send({ type: "approval.decision", id: approval.id, approved, edited, reason });
    setApproval(null);
  }

  function chooseDecision(choice: string) {
    send({ type: "decision", choice });
    setDecision(null);
  }

  function toggleReads(value: boolean) {
    setAutoApproveReads(value);
    send({ type: "mode.set", auto_approve_reads: value });
  }

  function submitActivity(edited: ActivityDraft) {
    send({ type: "submit_activity", activity: edited });
  }

  async function submitManualReport(fields: ActivityDraft, outcome: ManualReportOutcome) {
    if (!runId) return;
    const res = await api.submitManualReport(runId, fields, outcome);
    setSubmittedId(res.activity_id ?? -1);
    setActivityDraft(fields);
    setPhase("DONE");
    if (ticket) setTicket({ ...ticket, status: res.status });
  }

  // Rebuild the controller on every render so closures inside voice tools always
  // see the latest phase/hypotheses/approval/decision/activityDraft values.
  controllerRef.current = {
    getStatus: () => {
      const lines: string[] = [`Phase: ${phase}.`];
      if (approval) {
        const cmd = approval.payload?.command ?? approval.payload?.commands?.join("; ") ?? "";
        lines.push(`Pending approval (${approval.kind}): ${cmd || "(see UI)"}.`);
      } else {
        lines.push("No approval pending.");
      }
      if (decision) {
        lines.push(`Decision needed: ${decision.question} Options: ${decision.options.join(", ")}.`);
      } else {
        lines.push("No decision pending.");
      }
      if (hypotheses.length > 0) {
        const top = hypotheses
          .slice(0, 3)
          .map((h) => `#${h.rank} ${h.title}${h.likelihood != null ? ` (${Math.round(h.likelihood)}%)` : ""}`)
          .join("; ");
        lines.push(`${hypotheses.length} hypotheses: ${top}.`);
      } else {
        lines.push("No hypotheses yet.");
      }
      lines.push(activityDraft ? "Activity draft is ready to submit." : "No activity draft yet.");
      lines.push(`Auto-approve reads: ${autoApproveReads ? "on" : "off"}.`);
      return lines.join(" ");
    },

    approve: (edited?: string) => {
      if (!approval) return "No approval is pending.";
      decideApproval(true, edited);
      return `Approved${edited ? ` with edited command: ${edited}` : ""}.`;
    },

    reject: (reason?: string) => {
      if (!approval) return "No approval is pending.";
      decideApproval(false);
      return `Rejected.${reason ? ` Reason noted: ${reason}` : ""}`;
    },

    selectHypothesis: (rankOrTitle: string) => {
      const resolved = resolveHypothesis(rankOrTitle);
      if (!resolved) return `No hypothesis found matching "${rankOrTitle}".`;
      selectHypothesis(resolved.id);
      return `Selected hypothesis #${resolved.rank}: ${resolved.title}.`;
    },

    commentHypothesis: (rankOrTitle: string, text: string) => {
      const resolved = resolveHypothesis(rankOrTitle);
      if (!resolved) return `No hypothesis found matching "${rankOrTitle}".`;
      commentHypothesis(resolved.id, text);
      return `Comment added to hypothesis #${resolved.rank}.`;
    },

    submitHypothesis: (title: string, reasoning: string, checks: string[]) => {
      submitOwnHypothesis({ title, reasoning, checks });
      return `Hypothesis "${title}" submitted for checking.`;
    },

    answerDecision: (choice: string) => {
      if (!decision) return "No decision is pending.";
      const lower = choice.toLowerCase();
      // Prefer an exact case-insensitive match; fall back to substring match.
      const matched =
        decision.options.find((o) => o.toLowerCase() === lower) ??
        decision.options.find((o) => o.toLowerCase().includes(lower));
      if (!matched) {
        return `Option "${choice}" not found. Available: ${decision.options.join(", ")}.`;
      }
      chooseDecision(matched);
      return `Chose: ${matched}.`;
    },

    setAutoApproveReads: (value: boolean) => {
      toggleReads(value);
      return `Auto-approve reads set to ${value ? "on" : "off"}.`;
    },

    runCommand: (command: string) => {
      send({ type: "terminal.input", command });
      return `Sent \`${command}\` through the safety gate.`;
    },

    submitActivity: () => {
      if (!activityDraft) return "No activity draft to submit yet.";
      submitActivity(activityDraft);
      return "Activity draft submitted.";
    },

    stopRun: () => {
      send({ type: "stop" });
      return "Stop signal sent.";
    },
  };

  // Helper: resolve a hypothesis by 1-based rank number or case-insensitive title
  // substring — used by the voice approve/comment tools.
  function resolveHypothesis(rankOrTitle: string): Hypothesis | undefined {
    const trimmed = rankOrTitle.trim();
    const asNum = parseInt(trimmed, 10);
    if (!isNaN(asNum)) {
      return hypotheses.find((h) => h.rank === asNum);
    }
    const lower = trimmed.toLowerCase();
    return hypotheses.find((h) => h.title.toLowerCase().includes(lower));
  }

  const busy = BUSY_PHASES.includes(phase);
  const terminalEnabled = !!approval || !busy;
  const runActive = !FINAL_PHASES.includes(phase);

  // Right panel adapts to the phase: the full ranked list only while choosing a
  // hypothesis; during CHECK just the active one; nothing during fix/validate/etc.
  const awaitingSelection = phase === "HYPOTHESES";
  const activeHypo =
    hypotheses.find((h) => h.status === "checking") ||
    hypotheses.find((h) => h.status === "confirmed");

  let hypothesisPanel: JSX.Element | null = null;
  if (awaitingSelection) {
    hypothesisPanel = (
      <HypothesisList
        hypotheses={hypotheses}
        mode="select"
        onSelect={selectHypothesis}
        onComment={commentHypothesis}
        onSubmitOwn={submitOwnHypothesis}
      />
    );
  } else if (phase === "CHECK" && activeHypo) {
    hypothesisPanel = (
      <HypothesisList
        hypotheses={[activeHypo]}
        mode="active"
        onSelect={selectHypothesis}
        onComment={commentHypothesis}
        onSubmitOwn={submitOwnHypothesis}
      />
    );
  }

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
        reportDisabled={submittedId !== null}
        onToggleReads={toggleReads}
        onStop={() => send({ type: "stop" })}
        onReport={() => setReportOpen(true)}
      />

      <ManualReportModal
        open={reportOpen}
        ticketTitle={ticket ? `#${ticket.id} · ${ticket.title}` : undefined}
        onClose={() => setReportOpen(false)}
        onSubmit={submitManualReport}
      />
      {/* Voice panel: additive hands-free mode. Only rendered when the backend
          reports voice is configured (voiceEnabled flag). */}
      {voiceEnabled && <VoiceControl {...voice} />}

      <div className="workspace-grid">
        <div>
          <TerminalView
            chunks={termChunks}
            enabled={terminalEnabled}
            onData={(data) =>
              send({
                type: "terminal.data",
                data,
                cols: termSize.current.cols,
                rows: termSize.current.rows,
              })
            }
            onResize={(cols, rows) => {
              termSize.current = { cols, rows };
              send({ type: "terminal.resize", cols, rows });
            }}
          />
          <AgentActivity events={activity} />
        </div>

        <div>
          {decision && <DecisionPrompt decision={decision} onChoose={chooseDecision} />}
          {approval && <ApprovalPrompt approval={approval} onDecide={decideApproval} />}
          {hypothesisPanel}
          {activityDraft && (
            <ActivityReview
              draft={activityDraft}
              submittedId={submittedId}
              submittedStatus={ticket?.status}
              onSubmit={submitActivity}
            />
          )}
          {!decision && !approval && !hypothesisPanel && !activityDraft && (
            <div className="notice">{phaseHint(phase)}</div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The single live stream of what the agent is doing, in the technician's words:
 * its thoughts, the commands it runs (gray, no output), and run milestones. Raw
 * command output and bare tool-call plumbing are intentionally left out — those
 * live in the Terminal above. Auto-scrolls to the newest line.
 */
function AgentActivity({ events }: { events: RunEvent[] }) {
  const bodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  const tokens = [...events]
    .reverse()
    .find((e) => e.type === "agent.message" && e.context_tokens != null);

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Agent Activity</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {tokens?.context_tokens != null
            ? `~${tokens.context_tokens.toLocaleString()} ctx tokens` +
              (tokens.compactions ? ` · ${tokens.compactions} compaction(s)` : "")
            : "continuous agent"}
        </span>
      </div>
      <div
        ref={bodyRef}
        className="panel-body"
        style={{ maxHeight: 420, overflow: "auto", fontSize: 13 }}
      >
        {events.length === 0 && <div className="muted">No activity yet.</div>}
        {events.map((ev, i) => (
          <ActivityLine key={i} ev={ev} />
        ))}
      </div>
    </div>
  );
}

function ActivityLine({ ev }: { ev: RunEvent }) {
  switch (ev.type) {
    case "error":
      return <div className="notice error">{ev.message}</div>;

    case "validation.result":
      return (
        <div className={`notice ${ev.success ? "success" : "warning"}`}>
          Validation {ev.after_restart ? "(after restart) " : ""}
          {ev.success ? "passed" : "failed"}: {ev.proof}
        </div>
      );

    case "command.run": {
      // Show the command the agent ran, in gray — but never its output (that's
      // the terminal's job). The agent tends to put its thinking in the command's
      // `purpose`, so surface that as a thought line above the command (otherwise
      // it would only ever appear in the approval card). Blocked/rejected commands
      // get a small status tag.
      if (!ev.command) return null;
      const status = ev.blocked
        ? " (blocked)"
        : ev.rejected
        ? " (rejected)"
        : ev.edited_from
        ? " (edited by you)"
        : "";
      const purpose = (ev.purpose || "").trim();
      return (
        <div style={{ margin: "6px 0", paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
          {purpose && <div style={{ whiteSpace: "pre-wrap", marginBottom: 2 }}>{purpose}</div>}
          <div
            className="muted mono"
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: 12,
            }}
          >
            $ {ev.command}
            {status}
          </div>
          {ev.rejected && ev.reason && (
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              reason: {ev.reason}
            </div>
          )}
          {ev.edited_from && (
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              was: <span className="mono">{ev.edited_from}</span>
            </div>
          )}
        </div>
      );
    }

    case "agent.message": {
      // The agent's user-facing narration + thinking. Raw tool results and bare
      // tool-call intentions are deliberately omitted from this stream.
      if (ev.kind === "tool_result") return null;
      const reasoning = (ev.reasoning || "").trim();
      const text = (ev.text || "").trim();
      if (!reasoning && !text) return null;
      return (
        <div
          style={{
            margin: "6px 0",
            paddingLeft: 8,
            borderLeft: "2px solid var(--border)",
          }}
        >
          {reasoning && <ThinkingBlock reasoning={reasoning} />}
          {text && <div style={{ whiteSpace: "pre-wrap" }}>{text}</div>}
        </div>
      );
    }

    case "run.state":
      return (
        <div
          className="muted"
          style={{
            margin: "8px 0 4px",
            paddingTop: 6,
            borderTop: "1px solid var(--border)",
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: 0.5,
          }}
        >
          Phase: {ev.phase}
        </div>
      );

    case "info":
      return <div style={{ padding: "2px 0" }}>{ev.text}</div>;

    default:
      return null;
  }
}

/**
 * Agent reasoning, collapsed by default so the activity log stays scannable.
 * Click the summary line to reveal the full thinking text.
 */
function ThinkingBlock({ reasoning }: { reasoning: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="muted" style={{ fontStyle: "italic" }}>
      <div
        onClick={() => setOpen((o) => !o)}
        style={{ cursor: "pointer", userSelect: "none" }}
        title={open ? "Hide thinking" : "Show thinking"}
      >
        <span style={{ display: "inline-block", width: 12, fontStyle: "normal" }}>
          {open ? "▾" : "▸"}
        </span>
        🧠 Thinking
      </div>
      {open && (
        <div style={{ whiteSpace: "pre-wrap", paddingLeft: 12, marginTop: 2 }}>
          {reasoning}
        </div>
      )}
    </div>
  );
}

function phaseHint(phase: string): string {
  switch (phase) {
    case "CONNECTING":
      return "Waiting to connect to the customer VM…";
    case "RECON":
      return "Agent is gathering read-only diagnostics…";
    case "REPRODUCING":
      return "Agent is trying to reproduce the reported problem…";
    case "INVESTIGATING":
      return "Agent is investigating autonomously — watch the terminal…";
    case "AWAITING_INPUT":
      return "Agent is waiting for your decision…";
    case "SHELL":
      return "Plain SSH session — the agent is NOT running. Drive the terminal yourself.";
    case "CHECK":
      return "Agent is checking the selected hypothesis…";
    case "FIX_PROPOSE":
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
