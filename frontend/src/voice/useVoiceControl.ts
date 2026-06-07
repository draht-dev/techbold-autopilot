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
const SYSTEM_PROMPT = `You are a calm, efficient co-pilot voice assistant for a Linux service-desk technician running an autonomous AI troubleshooting session.
You can read the current run state at any time using the get_run_status tool.
You can steer the run using the other available tools: approve/reject commands, pick or comment on hypotheses, answer decision prompts, toggle auto-approve, send commands, submit the activity report, or stop the run.
Rules:
- Always call get_run_status first if you are unsure what is happening.
- For approve_action: read the pending command back to the technician and ask for explicit verbal confirmation before approving — this is a safety gate.
- Never invent terminal output or results; only report what the tools return.
- Keep responses concise and actionable.
- When you receive a contextual update about a pending approval or decision, proactively inform the technician and offer to help.`.trim();

export function useVoiceControl(
  controllerRef: React.MutableRefObject<VoiceController>
): UseVoiceControlReturn {
  const [error, setError] = useState<string | null>(null);

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
        checks: string[];
      }) =>
        controllerRef.current.submitHypothesis(
          params.title,
          params.reasoning,
          params.checks ?? []
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
    overrides: {
      agent: {
        prompt: { prompt: SYSTEM_PROMPT },
        firstMessage:
          "Voice control connected. Say 'status' any time to hear where the run stands.",
        language: "en",
      },
    },
    onError: (message: unknown) => {
      setError(typeof message === "string" ? message : "Voice agent error");
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
