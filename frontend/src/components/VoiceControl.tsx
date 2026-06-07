// Compact voice-control panel that sits alongside RunControls.
// It mirrors the existing minimalist panel/badge/button style.

import type { UseVoiceControlReturn } from "../voice/useVoiceControl";

type Props = UseVoiceControlReturn;

export default function VoiceControl({
  status,
  isSpeaking,
  error,
  active,
  start,
  stop,
}: Props) {
  // Derive a short human-readable state label so the technician always knows
  // what the mic is doing without needing to interpret raw status strings.
  function statusLabel() {
    if (status === "connecting") return "connecting…";
    if (!active) return "idle";
    if (isSpeaking) return "speaking";
    return "listening";
  }

  // Badge colour follows the same convention as the rest of the UI:
  // "done" (green) while the agent is speaking; "open" (accent blue) while
  // connected/connecting but listening; no badge modifier when disconnected.
  function badgeClass() {
    if (!active && status !== "connecting") return "";
    if (isSpeaking) return "done";
    return "open";
  }

  return (
    <div className="panel" style={{ marginTop: 8 }}>
      <div
        className="panel-body"
        style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}
      >
        <div>
          <label>Voice</label>
          <span className={`badge ${badgeClass()}`} style={{ marginLeft: 6 }}>
            {statusLabel()}
          </span>
          {isSpeaking && (
            <span style={{ marginLeft: 6, fontSize: 12 }}>🔊</span>
          )}
        </div>

        {active ? (
          <button className="danger" onClick={stop}>
            Disconnect mic
          </button>
        ) : (
          <button
            className="primary"
            onClick={start}
            disabled={status === "connecting"}
          >
            {status === "connecting" ? "Connecting…" : "Connect mic"}
          </button>
        )}

        {error && (
          <span className="notice error" style={{ padding: "2px 8px", margin: 0, flex: 1 }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
