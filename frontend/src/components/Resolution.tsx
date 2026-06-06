import { useState } from "react";
import { RunEvent, RunSnapshot } from "../api/client";
import Markdown from "./Markdown";

interface Props {
  resolution: RunSnapshot;
}

/**
 * Shows how a finished ticket was resolved: the submitted solution (summary,
 * root cause, actions, validation) and — collapsed by default — the complete
 * troubleshooting log of the run that fixed it.
 */
export default function Resolution({ resolution }: Props) {
  const [showLog, setShowLog] = useState(false);
  const a = resolution.submitted_activity;
  const outcome = resolution.outcome || "resolved";

  const fields: Array<[string, string | undefined]> = [
    ["Summary", a?.summary],
    ["Root cause", a?.root_cause],
    ["Actions taken", a?.actions_taken],
    ["Commands", a?.commands_summary],
    ["Validation", a?.validation_result],
  ];

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-header">
        <h2>Resolution</h2>
        <span className={`badge ${outcome === "fixed" ? "done" : "open"}`}>{outcome}</span>
      </div>
      <div className="panel-body">
        <dl className="kv">
          {fields.map(([label, value]) =>
            value ? (
              <div key={label} style={{ display: "contents" }}>
                <dt>{label}</dt>
                <dd>
                  <Markdown>{value}</Markdown>
                </dd>
              </div>
            ) : null
          )}
        </dl>

        {resolution.events.length > 0 ? (
          <>
            <button
              className="ghost"
              style={{ marginTop: 12 }}
              onClick={() => setShowLog((v) => !v)}
            >
              {showLog ? "Hide" : "Show"} full troubleshooting log ({resolution.events.length})
            </button>

            {showLog && (
              <div
                className="panel-body"
                style={{
                  marginTop: 8,
                  maxHeight: 360,
                  overflow: "auto",
                  background: "var(--bg, #0d1117)",
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                {resolution.events.map((ev, i) => (
                  <LogLine key={i} ev={ev} />
                ))}
              </div>
            )}
          </>
        ) : (
          // The activity is persisted; the full log is kept in memory only and is
          // not retained once the run leaves memory / the backend restarts.
          <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
            The full troubleshooting log is kept in memory only and isn’t available
            after a backend restart.
          </p>
        )}
      </div>
    </div>
  );
}

function LogLine({ ev }: { ev: RunEvent }) {
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
      const out = ev.output_redacted || ev.output || "";
      const code = ev.exit_code != null ? ` (exit ${ev.exit_code})` : "";
      return (
        <pre className="mono" style={{ margin: "2px 0", whiteSpace: "pre-wrap" }}>
          $ {ev.command}
          {ev.actor ? `  [${ev.actor}]` : ""}
          {code}
          {out ? `\n${out}` : ""}
        </pre>
      );
    }
    case "agent.message": {
      const calls = (ev.tool_calls || [])
        .map((c: any) => `→ ${c.name}${c.args?.command ? `: ${c.args.command}` : ""}`)
        .join("\n");
      const text = [ev.reasoning && `🧠 ${ev.reasoning}`, ev.text, calls]
        .filter(Boolean)
        .join("\n");
      if (!text) return null;
      return (
        <div style={{ margin: "4px 0", whiteSpace: "pre-wrap", opacity: 0.9 }}>{text}</div>
      );
    }
    case "run.state":
      return (
        <div className="muted" style={{ margin: "4px 0" }}>
          — phase: {ev.phase} —
        </div>
      );
    case "info":
      return <div style={{ padding: "2px 0" }}>{ev.text}</div>;
    default:
      return ev.text ? <div style={{ padding: "2px 0" }}>{ev.text}</div> : null;
  }
}
