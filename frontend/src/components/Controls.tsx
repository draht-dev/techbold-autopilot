import React, { useState } from "react";
import { retry, abort, devReset } from "../api";

interface Props {
  sessionId: string;
  onAborted: () => void;
}

export default function Controls({ sessionId, onAborted }: Props) {
  const [retrying, setRetrying] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetMsg, setResetMsg] = useState<string | null>(null);

  async function handleRetry() {
    setRetrying(true);
    setError(null);
    try {
      await retry(sessionId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  }

  async function handleAbort() {
    if (!confirm("Abort the current session? This will stop the agent immediately.")) return;
    setAborting(true);
    setError(null);
    try {
      await abort(sessionId);
      onAborted();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setAborting(false);
    }
  }

  async function handleReset() {
    if (!confirm("Reset environment? This will clear activities and reboot VMs.")) return;
    setResetting(true);
    setResetMsg(null);
    setError(null);
    try {
      await devReset();
      setResetMsg("Environment reset successfully.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="controls-bar">
      <div className="controls-actions">
        <button
          className="btn btn-secondary"
          onClick={handleRetry}
          disabled={retrying || aborting}
        >
          {retrying ? "Retrying…" : "Retry"}
        </button>
        <button
          className="btn btn-danger"
          onClick={handleAbort}
          disabled={aborting || retrying}
        >
          {aborting ? "Aborting…" : "Abort / Stop"}
        </button>
        <button
          className="btn btn-dev"
          onClick={handleReset}
          disabled={resetting}
          title="Dev only: reset environment"
        >
          {resetting ? "Resetting…" : "Reset environment (dev)"}
        </button>
      </div>

      {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}
      {resetMsg && <div className="success-banner" style={{ marginTop: 8 }}>{resetMsg}</div>}
    </div>
  );
}
