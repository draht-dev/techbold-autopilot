import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface Props {
  chunks: string[];
  /** Forward keystrokes to the VM PTY only while it is the technician's turn. */
  enabled: boolean;
  onData: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
}

export default function TerminalView({ chunks, enabled, onData, onResize }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const writtenRef = useRef(0);
  // Keep the latest handlers/flags reachable from the xterm callbacks.
  const enabledRef = useRef(enabled);
  const onDataRef = useRef(onData);
  const onResizeRef = useRef(onResize);
  enabledRef.current = enabled;
  onDataRef.current = onData;
  onResizeRef.current = onResize;

  useEffect(() => {
    const term = new XTerm({
      convertEol: true,
      fontFamily: "Menlo, Monaco, 'Courier New', monospace",
      fontSize: 12,
      cursorBlink: true,
      // stdin is ENABLED so full-screen programs (vim, htop, less) work.
      theme: { background: "#1e1e1e", foreground: "#d4d4d4" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current!);
    const safeFit = () => {
      try {
        fit.fit();
        onResizeRef.current(term.cols, term.rows);
      } catch {
        /* ignore */
      }
    };
    safeFit();
    termRef.current = term;
    fitRef.current = fit;

    // Raw keystrokes -> backend PTY (only when it is the technician's turn).
    const dataSub = term.onData((data) => {
      if (enabledRef.current) onDataRef.current(data);
    });
    const resizeSub = term.onResize(({ cols, rows }) => onResizeRef.current(cols, rows));
    window.addEventListener("resize", safeFit);

    return () => {
      window.removeEventListener("resize", safeFit);
      dataSub.dispose();
      resizeSub.dispose();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      writtenRef.current = 0;
    };
  }, []);

  // Stream agent command echoes + live PTY output into the terminal.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenRef.current; i < chunks.length; i++) {
      term.write(chunks[i]);
    }
    writtenRef.current = chunks.length;
  }, [chunks]);

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Terminal</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {enabled ? "interactive — vim, htop & friends work here" : "agent is working…"}
        </span>
      </div>
      <div
        ref={containerRef}
        onClick={() => termRef.current?.focus()}
        style={{ height: 380, padding: 8, background: "#1e1e1e", opacity: enabled ? 1 : 0.85 }}
      />
    </div>
  );
}
