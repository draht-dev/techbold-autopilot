import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

interface Props {
  chunks: string[];
  enabled: boolean;
  onCommand: (command: string) => void;
}

export default function TerminalView({ chunks, enabled, onCommand }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const writtenRef = useRef(0);
  const [cmd, setCmd] = useState("");

  useEffect(() => {
    const term = new XTerm({
      convertEol: true,
      fontFamily: "Menlo, Monaco, 'Courier New', monospace",
      fontSize: 12,
      cursorBlink: false,
      disableStdin: true,
      theme: { background: "#1e1e1e", foreground: "#d4d4d4" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current!);
    try {
      fit.fit();
    } catch {
      /* ignore */
    }
    termRef.current = term;
    const onResize = () => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      term.dispose();
      termRef.current = null;
      writtenRef.current = 0;
    };
  }, []);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenRef.current; i < chunks.length; i++) {
      term.write(chunks[i]);
    }
    writtenRef.current = chunks.length;
  }, [chunks]);

  function submit() {
    const value = cmd.trim();
    if (!value) return;
    onCommand(value);
    setCmd("");
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Terminal</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {enabled
            ? "non-interactive — one command at a time"
            : "agent is working…"}
        </span>
      </div>
      <div ref={containerRef} style={{ height: 360, padding: 8, background: "#1e1e1e" }} />
      <div style={{ display: "flex", gap: 8, padding: 8, borderTop: "1px solid var(--border)" }}>
        <input
          type="text"
          className="mono"
          placeholder={enabled ? "Run a command on the VM…" : "Disabled while the agent runs"}
          value={cmd}
          disabled={!enabled}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button onClick={submit} disabled={!enabled}>
          Run
        </button>
      </div>
    </div>
  );
}
