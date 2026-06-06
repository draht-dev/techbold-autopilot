"""Continuous, tool-calling agent session with auto-compaction.

One troubleshooting run is a SINGLE agent conversation. The orchestration loop
appends user turns and tool results, then calls the model (with tools bound) over
the full history, so the agent keeps a continuous memory of its own investigation
and decides for itself when to act, iterate, or finish.

When the context grows past a configured share of the model's window, older turns
are summarised into one compact block. We never split an assistant tool-call
message from its tool results, so the compacted history stays valid for the API.
There is no provider-native "compact" we can rely on through OpenRouter (its
context-compression plugin does lossy middle-out truncation aimed at tiny-context
models), so compaction is done here with the fast model.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.llm import LLM, LLMError

COMPACT_SUMMARY_SYSTEM = """You compress a Linux service-desk troubleshooting conversation.
Preserve everything needed to continue the investigation:
- ticket symptoms and system details
- whether the problem was reproduced, and how
- recon findings and command outputs (key lines only)
- hypotheses proposed, rejected, or confirmed
- technician selections, comments, and decisions
- fixes applied and validation results
Be concise but technically precise. No secrets."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4). Good enough for compaction triggers."""
    return max(1, len(text) // 4)


def _message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "")


def _tool_calls(msg: BaseMessage) -> list[dict[str, Any]]:
    return list(getattr(msg, "tool_calls", None) or [])


def _render_message(msg: BaseMessage) -> str:
    """Render one message (including tool calls / results) for the summary input."""
    if isinstance(msg, SystemMessage):
        role = "SYSTEM"
    elif isinstance(msg, HumanMessage):
        role = "USER"
    elif isinstance(msg, ToolMessage):
        role = "TOOL_RESULT"
    elif isinstance(msg, AIMessage):
        role = "ASSISTANT"
    else:
        role = type(msg).__name__.replace("Message", "").upper()

    parts: list[str] = []
    text = _message_text(msg).strip()
    if text:
        parts.append(text)
    for call in _tool_calls(msg):
        name = call.get("name", "tool")
        args = call.get("args", {})
        parts.append(f"[tool_call {name} {args}]")
    body = "\n".join(parts) if parts else "(empty)"
    return f"{role}: {body}"


def _messages_token_estimate(messages: Sequence[BaseMessage]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(_message_text(m))
        for call in _tool_calls(m):
            total += estimate_tokens(str(call.get("args", "")))
    return total


class AgentSession:
    """One continuous tool-calling conversation for a single troubleshooting run."""

    def __init__(
        self,
        llm: LLM,
        system: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 200_000,
        compact_threshold: float = 0.8,
        keep_recent_messages: int = 20,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.model = model or llm.agent_model
        self.max_tokens = max_tokens
        self.compact_threshold = compact_threshold
        self.keep_recent_messages = keep_recent_messages
        self.reasoning_effort = reasoning_effort
        self.compaction_count = 0
        # The last prompt-token count the provider reported (most accurate trigger).
        self.last_prompt_tokens = 0
        self.messages: list[BaseMessage] = [SystemMessage(content=system)]

    # ------------------------------------------------------------------ #
    # History mutation
    # ------------------------------------------------------------------ #
    def add_user(self, text: str) -> None:
        self.messages.append(HumanMessage(content=text))

    def add_tool_result(self, tool_call_id: str, content: str, *, name: Optional[str] = None) -> None:
        self.messages.append(
            ToolMessage(content=content or "(no output)", tool_call_id=tool_call_id, name=name)
        )

    # ------------------------------------------------------------------ #
    # Context accounting
    # ------------------------------------------------------------------ #
    @property
    def estimated_tokens(self) -> int:
        return _messages_token_estimate(self.messages)

    @property
    def context_tokens(self) -> int:
        """Best available context size: max of the reported prompt size and the
        forward-looking estimate (which also covers freshly-appended turns)."""
        return max(self.estimated_tokens, self.last_prompt_tokens)

    # ------------------------------------------------------------------ #
    # Model invocation
    # ------------------------------------------------------------------ #
    async def invoke(self, tools: Sequence[Any]) -> AIMessage:
        """Compact if needed, call the tool-calling model, record + return the reply."""
        await self._compact_if_needed()
        ai = await self.llm.complete_with_tools(
            self.messages,
            tools,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
        )
        self.messages.append(ai)
        usage = getattr(ai, "usage_metadata", None)
        if isinstance(usage, dict) and usage.get("input_tokens"):
            self.last_prompt_tokens = int(usage.get("input_tokens", 0)) + int(
                usage.get("output_tokens", 0)
            )
        else:
            self.last_prompt_tokens = self.estimated_tokens
        return ai

    # ------------------------------------------------------------------ #
    # Compaction
    # ------------------------------------------------------------------ #
    async def _compact_if_needed(self) -> None:
        limit = int(self.max_tokens * self.compact_threshold)
        if self.context_tokens <= limit:
            return
        await self._compact()

    @staticmethod
    def _safe_cut(body: list[BaseMessage], desired: int) -> int:
        """Advance ``desired`` forward until the kept tail does not begin with a
        ToolMessage. This keeps every assistant tool-call message together with
        its tool results (orphaned tool results would be rejected by the API)."""
        cut = max(0, min(desired, len(body)))
        while cut < len(body) and isinstance(body[cut], ToolMessage):
            cut += 1
        return cut

    async def _compact(self) -> None:
        system = self.messages[0]
        body = self.messages[1:]
        if len(body) <= self.keep_recent_messages:
            return

        cut = self._safe_cut(body, len(body) - self.keep_recent_messages)
        if cut >= len(body):
            # The whole tail is one giant tool turn; nothing safe to summarise.
            return
        to_summarize = body[:cut]
        tail = body[cut:]
        if not to_summarize:
            return

        transcript = "\n\n".join(_render_message(m) for m in to_summarize)
        try:
            summary = await self.llm.complete_text(
                COMPACT_SUMMARY_SYSTEM,
                f"Summarize this conversation segment:\n\n{transcript}",
                model=self.llm.fast_model,
                temperature=0.1,
            )
        except LLMError:
            summary = transcript[:4000] + "\n[...truncated...]"

        self.compaction_count += 1
        self.last_prompt_tokens = 0  # force a fresh estimate after compaction
        self.messages = [
            system,
            HumanMessage(
                content=(
                    f"[SESSION COMPACTED - pass {self.compaction_count}]\n"
                    f"Summary of earlier investigation:\n{summary.strip()}"
                )
            ),
            AIMessage(
                content=(
                    "Understood. I have the compacted history and will continue "
                    "the investigation from here."
                )
            ),
            *tail,
        ]

    # ------------------------------------------------------------------ #
    def snapshot(self) -> list[dict[str, Any]]:
        """Serializable view for debugging / audit."""
        out: list[dict[str, Any]] = []
        for msg in self.messages:
            role = type(msg).__name__.replace("Message", "").lower()
            entry: dict[str, Any] = {"role": role, "content": _message_text(msg)}
            calls = _tool_calls(msg)
            if calls:
                entry["tool_calls"] = [
                    {"name": c.get("name"), "args": c.get("args")} for c in calls
                ]
            out.append(entry)
        return out
