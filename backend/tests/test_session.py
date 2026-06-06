"""Continuous tool-calling session: history growth, compaction, tool-pair safety."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.session import AgentSession, estimate_tokens
from app.config import Settings


class FakeLLM:
    configured = True
    agent_model = "agent"
    fast_model = "fast"

    def __init__(self) -> None:
        self.summarize_calls = 0

    async def complete_with_tools(self, messages, tools, model=None, reasoning_effort=None):
        return AIMessage(content="ok")

    async def complete_text(self, system, user, model=None, temperature=0.2):
        self.summarize_calls += 1
        return "Earlier: recon showed nginx failed."


def test_estimate_tokens():
    assert estimate_tokens("abcd") >= 1


async def test_session_compacts_when_over_threshold(tmp_path):
    settings = Settings(
        audit_dir=str(tmp_path),
        agent_context_max_tokens=200,
        agent_context_compact_threshold=0.5,
        agent_context_keep_recent_messages=2,
    )
    llm = FakeLLM()
    session = AgentSession(
        llm,
        "system prompt",
        max_tokens=settings.agent_context_max_tokens,
        compact_threshold=settings.agent_context_compact_threshold,
        keep_recent_messages=settings.agent_context_keep_recent_messages,
    )

    big = "x" * 800
    session.add_user(big)
    session.messages.append(AIMessage(content='{"ok": true}'))
    session.add_user(big)
    session.messages.append(AIMessage(content='{"ok": true}'))

    assert session.estimated_tokens > 100
    await session._compact_if_needed()

    assert session.compaction_count == 1
    assert llm.summarize_calls == 1
    assert any(
        "SESSION COMPACTED" in (m.content if isinstance(m.content, str) else "")
        for m in session.messages
        if isinstance(m, HumanMessage)
    )
    assert len(session.messages) <= settings.agent_context_keep_recent_messages + 3


async def test_compaction_never_orphans_a_tool_result(tmp_path):
    """The kept tail must not begin with a ToolMessage (its tool-call AIMessage
    would otherwise be summarised away, leaving an orphaned result)."""
    llm = FakeLLM()
    session = AgentSession(
        llm, "system", max_tokens=100, compact_threshold=0.5, keep_recent_messages=2
    )
    session.add_user("investigate")
    session.messages.append(
        AIMessage(content="", tool_calls=[{"name": "RunCommand", "args": {"command": "ls"}, "id": "t1"}])
    )
    session.add_tool_result("t1", "x" * 800, name="RunCommand")
    session.messages.append(AIMessage(content="done"))

    # Naive cut (len-2) would land on the ToolMessage; the safe cut must advance.
    await session._compact()

    assert session.compaction_count == 1
    preamble = session.messages[:3]  # System, Human(summary), AI(understood)
    tail = session.messages[3:]
    assert tail, "compaction kept no recent messages"
    assert not isinstance(tail[0], ToolMessage)
    assert isinstance(preamble[0].content, str)
