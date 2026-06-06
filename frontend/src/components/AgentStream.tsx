import { useEffect, useRef } from "react";
import { AgentMessage } from "../api/client";

interface Props {
  messages: AgentMessage[];
}

/**
 * A small dev window that streams the ONE continuous agent's live thinking,
 * tool calls, and tool results. Purely for development visibility — every
 * command is still gated/audited and mirrored into the real terminal.
 */
export default function AgentStream({ messages }: Props) {
  const bodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const last = messages[messages.length - 1];
  const tokens = [...messages].reverse().find((m) => m.context_tokens != null);

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Agent Stream</h2>
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
        style={{ maxHeight: 260, overflow: "auto", fontSize: 13 }}
      >
        {messages.length === 0 && <div className="muted">Agent has not spoken yet.</div>}
        {messages.map((m, i) => (
          <AgentLine key={i} m={m} highlight={m === last} />
        ))}
      </div>
    </div>
  );
}

function AgentLine({ m, highlight }: { m: AgentMessage; highlight: boolean }) {
  if (m.kind === "tool_result") {
    return (
      <div style={{ margin: "4px 0", opacity: 0.85 }}>
        <span className="badge open" style={{ fontSize: 10 }}>
          {m.name || "tool"} →
        </span>
        <pre
          className="mono"
          style={{
            margin: "2px 0 0",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontSize: 12,
          }}
        >
          {m.text}
        </pre>
      </div>
    );
  }

  return (
    <div
      style={{
        margin: "6px 0",
        paddingLeft: 8,
        borderLeft: `2px solid ${highlight ? "var(--accent)" : "var(--border)"}`,
      }}
    >
      {m.reasoning && (
        <div className="muted" style={{ fontStyle: "italic", whiteSpace: "pre-wrap" }}>
          🧠 {m.reasoning}
        </div>
      )}
      {m.text && <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>}
      {(m.tool_calls || []).map((c, i) => (
        <div key={i} className="mono" style={{ marginTop: 4, fontSize: 12 }}>
          → {c.name}
          {c.args?.command ? `: ${c.args.command}` : ""}
          {c.args?.question ? `: ${c.args.question}` : ""}
          {c.args?.outcome ? `: ${c.args.outcome}` : ""}
        </div>
      ))}
    </div>
  );
}
