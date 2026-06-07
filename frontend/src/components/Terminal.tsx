import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef } from "react";

interface Props {
  chunks: string[];
  /** Forward keystrokes to the VM PTY only while it is the technician's turn. */
  enabled: boolean;
  onData: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
}

export default function TerminalView({
  chunks,
  enabled,
  onData,
  onResize,
}: Props) {
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
    termRef.current = term;
    fitRef.current = fit;

    // Fit once the panel has its final layout (new tabs / grid columns often report
    // zero width on the first paint, which leaves a blank-looking terminal).
    const ro = new ResizeObserver(() => safeFit());
    ro.observe(containerRef.current!);
    requestAnimationFrame(() => requestAnimationFrame(safeFit));
    window.addEventListener("resize", safeFit);

    // Raw keystrokes -> backend PTY (only when it is the technician's turn).
    const dataSub = term.onData((data) => {
      if (enabledRef.current) onDataRef.current(data);
    });
    const resizeSub = term.onResize(({ cols, rows }) =>
      onResizeRef.current(cols, rows),
    );

    return () => {
      ro.disconnect();
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
    const fit = fitRef.current;
    if (!term) return;
    const prev = writtenRef.current;
    for (let i = prev; i < chunks.length; i++) {
      term.write(chunks[i]);
    }
    writtenRef.current = chunks.length;
    if (chunks.length > prev) {
      try {
        fit?.fit();
      } catch {
        /* ignore */
      }
    }
  }, [chunks]);

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Terminal</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {enabled ? "interactive" : "agent is working…"}
        </span>
      </div>
      <div
        ref={containerRef}
        onClick={() => termRef.current?.focus()}
        style={{
          height: 380,
          padding: 8,
          background: "#1e1e1e",
          opacity: enabled ? 1 : 0.85,
        }}
      />
    </div>
  );
}
