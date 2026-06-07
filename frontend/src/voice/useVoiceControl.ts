// Voice control hook: bridges the ElevenLabs conversational agent to the run's
// steering actions. Each client tool delegates to a VoiceController ref so the
// hook always sees the latest Workspace state without stale closures.

import { useCallback, useRef, useState } from "react";
import { useConversation } from "@elevenlabs/react";
import { api } from "../api/client";

export interface VoiceController {
  /** Returns a human-readable summary of current run state for the voice agent. */
  getStatus: () => string;
  /** Approve the pending command; optionally supply an edited command text. */
  approve: (edited?: string) => string;
  /** Reject the pending command; reason is informational only. */
  reject: (reason?: string) => string;
  /** Select a hypothesis by its 1-based rank number or a title substring. */
  selectHypothesis: (rankOrTitle: string) => string;
  /** Add a comment to a hypothesis identified by rank or title substring. */
  commentHypothesis: (rankOrTitle: string, text: string) => string;
  /** Propose a brand-new hypothesis (from the technician). */
  submitHypothesis: (title: string, reasoning: string, checks: string[]) => string;
  /** Pick one of the decision options (case-insensitive substring match). */
  answerDecision: (choice: string) => string;
  /** Toggle auto-approve for safe read-only commands. */
  setAutoApproveReads: (value: boolean) => string;
  /** Send a one-shot command through the existing safety gate (terminal.input). */
  runCommand: (command: string) => string;
  /** Submit the current activity draft as-is. */
  submitActivity: () => string;
  /** Stop the run entirely. */
  stopRun: () => string;
}

export interface UseVoiceControlReturn {
  /** Current ElevenLabs connection status. */
  status: "disconnected" | "connecting" | "connected" | "error";
  /** True while the agent is speaking audio. */
  isSpeaking: boolean;
  /** Error message from the last failed start attempt, or null. */
  error: string | null;
  /** Convenience: status === "connected". */
  active: boolean;
  /** Start the voice session (fetches a signed URL then opens the WebSocket). */
  start: () => Promise<void>;
  /** End the voice session. */
  stop: () => void;
  /**
   * Push a context update to the agent so it can react (e.g. speak up about a
   * pending approval) without being asked. No-ops when not connected.
   */
  notify: (text: string) => void;
}

// The system prompt explains the agent's role concisely — instruct it to use
// tools rather than invent information, and to confirm destructive actions.
// Conversation overrides (custom system prompt) are REJECTED at runtime unless the
// agent has that field enabled under Security → Overrides in the ElevenLabs
// dashboard — sending an unauthorized override closes the socket immediately
// ("Override for field 'prompt' is not allowed by config"), after which the SDK
// spams "WebSocket is already in CLOSING or CLOSED state" streaming mic audio into
// the dead socket. So by DEFAULT we send no overrides and rely on the agent's own
// dashboard config (system prompt is in docs/voice-agent.md to paste there). Opt in
// with VITE_VOICE_SEND_OVERRIDES=true ONLY if you've enabled the System prompt
// override toggle on the agent.
const SEND_OVERRIDES =
  (import.meta as any).env?.VITE_VOICE_SEND_OVERRIDES === "true";

const SYSTEM_PROMPT = `You are a calm, efficient co-pilot voice assistant for a Linux service-desk technician running an autonomous AI troubleshooting session.
You can read the current run state at any time using the get_run_status tool.
You can steer the run using the other available tools: approve/reject commands, pick or comment on hypotheses, answer decision prompts, toggle auto-approve, send commands, submit the activity report, or stop the run.
Rules:
- Always call get_run_status first if you are unsure what is happening.
- For approve_action: read the pending command back to the technician and ask for explicit verbal confirmation before approving — this is a safety gate.
- Never invent terminal output or results; only report what the tools return.
- Keep responses concise and actionable.
- When you receive a contextual update about a pending approval or decision, proactively inform the technician and offer to help.`.trim();

// Best-effort extraction of a human-readable reason from the SDK's onDisconnect
// payload. The SDK shape is roughly { reason: "error"|"agent"|"user", message?,
// context? } where `reason` is only the CATEGORY ("error") and the real cause
// lives in `message` or the nested `context` (a CloseEvent or Error). We dig past
// the category and, for a WebSocket CloseEvent, report its code + reason.
function extractReason(details: unknown): string {
  if (!details) return "";
  if (typeof details === "string") return details;
  if (typeof details !== "object") return "";
  const d = details as Record<string, unknown>;

  // Direct message on the payload.
  if (typeof d.message === "string" && d.message) return d.message;

  // Nested context: a CloseEvent ({ code, reason }) or an Error ({ message }).
  const ctx = d.context as Record<string, unknown> | undefined;
  if (ctx && typeof ctx === "object") {
    if (typeof ctx.reason === "string" && ctx.reason) {
      return ctx.code ? `${ctx.reason} (code ${ctx.code})` : ctx.reason;
    }
    if (typeof ctx.message === "string" && ctx.message) return ctx.message;
    if (typeof ctx.code === "number") return `close code ${ctx.code}`;
  }

  // Fall back to the category only if nothing better is available.
  if (typeof d.reason === "string" && d.reason && d.reason !== "error") {
    return d.reason;
  }
  return "";
}

export function useVoiceControl(
  controllerRef: React.MutableRefObject<VoiceController>
): UseVoiceControlReturn {
  const [error, setError] = useState<string | null>(null);
  // True while a stop() is in progress, so the resulting onDisconnect is treated
  // as intentional (not surfaced as an error).
  const intentionalStopRef = useRef(false);

  const conversation = useConversation({
    clientTools: {
      get_run_status: () => controllerRef.current.getStatus(),
      approve_action: (params: { edited_command?: string }) =>
        controllerRef.current.approve(params.edited_command),
      reject_action: (params: { reason?: string }) =>
        controllerRef.current.reject(params.reason),
      select_hypothesis: (params: { hypothesis: string }) =>
        controllerRef.current.selectHypothesis(params.hypothesis),
      comment_hypothesis: (params: { hypothesis: string; text: string }) =>
        controllerRef.current.commentHypothesis(params.hypothesis, params.text),
      submit_hypothesis: (params: {
        title: string;
        reasoning: string;
        // The agent tool declares `checks` as a single delimited string (primitive
        // types are the most portable for ElevenLabs tool params); split it here
        // into the list the controller expects. Tolerate an array too.
        checks?: string | string[];
      }) =>
        controllerRef.current.submitHypothesis(
          params.title,
          params.reasoning,
          Array.isArray(params.checks)
            ? params.checks
            : (params.checks ?? "")
                .split(/[;\n]/)
                .map((c) => c.trim())
                .filter(Boolean)
        ),
      answer_decision: (params: { choice: string }) =>
        controllerRef.current.answerDecision(params.choice),
      set_auto_approve_reads: (params: { enabled: boolean }) =>
        controllerRef.current.setAutoApproveReads(params.enabled),
      run_command: (params: { command: string }) =>
        controllerRef.current.runCommand(params.command),
      submit_activity: () => controllerRef.current.submitActivity(),
      stop_run: () => controllerRef.current.stopRun(),
    },
    // Only override the system prompt — the valuable part. We deliberately do NOT
    // override firstMessage / language: each overridden field must be separately
    // enabled under the agent's Security → Overrides, and sending one that isn't
    // authorized closes the socket ("Override for field 'X' is not allowed"). Set
    // the first message + language on the agent in the dashboard instead.
    ...(SEND_OVERRIDES
      ? { overrides: { agent: { prompt: { prompt: SYSTEM_PROMPT } } } }
      : {}),
    onError: (message: unknown) => {
      setError(typeof message === "string" ? message : "Voice agent error");
    },
    onDisconnect: (details?: unknown) => {
      // Surface why the server hung up. The most common cause is sending overrides
      // the agent hasn't authorized (Security → Overrides), which closes the socket
      // immediately after it opens. An intentional stop() is not an error.
      if (intentionalStopRef.current) {
        intentionalStopRef.current = false;
        return;
      }
      // Dump the full payload so the true close cause is visible in the console
      // (the in-UI banner only gets a short summary).
      try {
        console.error("[voice] disconnected:", JSON.stringify(details, Object.getOwnPropertyNames(details ?? {})), details);
      } catch {
        console.error("[voice] disconnected:", details);
      }
      const reason = extractReason(details);
      setError(
        reason
          ? `Voice disconnected: ${reason}`
          : "Voice disconnected unexpectedly. If this happened right after connecting, enable System prompt / First message / Language under your agent's Security → Overrides settings (or set VITE_VOICE_SEND_OVERRIDES=false)."
      );
    },
  });

  // Keep a stable ref to sendContextualUpdate so notify() doesn't go stale
  // when the component re-renders between the WS event and the notify call.
  const sendContextualUpdateRef = useRef(conversation.sendContextualUpdate);
  sendContextualUpdateRef.current = conversation.sendContextualUpdate;

  const status = conversation.status as
    | "disconnected"
    | "connecting"
    | "connected"
    | "error";
  const active = status === "connected";

  const start = useCallback(async () => {
    setError(null);
    try {
      const { signed_url } = await api.voiceSignedUrl();
      // HookOptions includes SessionConfig fields (signedUrl, connectionType)
      // so we pass the signed URL directly to startSession.
      conversation.startSession({
        signedUrl: signed_url,
        connectionType: "websocket",
      });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Failed to start voice session";
      // 503 from the backend means voice is not configured server-side.
      const userMsg = msg.includes("503")
        ? "Voice agent not configured on the server."
        : msg;
      setError(userMsg);
    }
  }, [conversation]);

  const stop = useCallback(() => {
    intentionalStopRef.current = true;
    conversation.endSession();
  }, [conversation]);

  const notify = useCallback(
    (text: string) => {
      // Guard: sendContextualUpdate is a no-op when disconnected per the SDK,
      // but we also check status so we don't spam the console when inactive.
      if (status !== "connected") return;
      sendContextualUpdateRef.current(text);
    },
    [status]
  );

  return { status, isSpeaking: conversation.isSpeaking, error, active, start, stop, notify };
}
