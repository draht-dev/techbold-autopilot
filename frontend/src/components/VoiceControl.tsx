// Floating action button (bottom-right) for hands-free voice control of the run.
// Idle: a blue mic FAB. Connected: turns into a live indicator (pulses while the
// agent speaks) with a small popover showing state + disconnect. Errors surface
// in the popover so the FAB itself stays clean.

import { useEffect, useState } from "react";
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
  // The popover auto-opens on connect and whenever there's an error to show.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (active) setOpen(true);
  }, [active]);
  useEffect(() => {
    if (error) setOpen(true);
  }, [error]);

  const connecting = status === "connecting";

  function statusLabel() {
    if (connecting) return "Connecting…";
    if (!active) return "Voice idle";
    if (isSpeaking) return "Speaking…";
    return "Listening…";
  }

  // FAB colour: accent blue idle/listening, green while speaking, red on error.
  const fabColor = error
    ? "var(--error)"
    : isSpeaking
    ? "var(--success)"
    : "var(--accent)";

  function onFabClick() {
    if (!active && !connecting) {
      start();
      setOpen(true);
    } else {
      setOpen((v) => !v);
    }
  }

  return (
    <div className="voice-fab-root">
      {open && (
        <div className="voice-fab-popover">
          <div className="voice-fab-popover-row">
            <span
              className="voice-fab-dot"
              style={{
                background: fabColor,
                animation: active && isSpeaking ? "voicePulse 1s ease-in-out infinite" : "none",
              }}
            />
            <strong style={{ fontSize: 13 }}>{statusLabel()}</strong>
          </div>

          {error && (
            <div className="notice error" style={{ margin: "8px 0 0", fontSize: 12 }}>
              {error}
            </div>
          )}

          {active ? (
            <button className="danger" style={{ marginTop: 10, width: "100%" }} onClick={stop}>
              Disconnect
            </button>
          ) : (
            <button
              className="primary"
              style={{ marginTop: 10, width: "100%" }}
              onClick={start}
              disabled={connecting}
            >
              {connecting ? "Connecting…" : "Connect voice"}
            </button>
          )}
        </div>
      )}

      <button
        type="button"
        className="voice-fab"
        onClick={onFabClick}
        aria-label={active ? "Voice control (connected)" : "Start voice control"}
        title={statusLabel()}
        style={{
          background: fabColor,
          animation: active && isSpeaking ? "voicePulse 1.4s ease-in-out infinite" : "none",
        }}
      >
        <MicIcon muted={!active && !connecting} />
      </button>
    </div>
  );
}

function MicIcon({ muted }: { muted: boolean }) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3Z"
        fill="currentColor"
      />
      <path
        d="M5 11a7 7 0 0 0 14 0M12 18v3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        fill="none"
      />
      {muted && (
        <path d="M4 4l16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      )}
    </svg>
  );
}
