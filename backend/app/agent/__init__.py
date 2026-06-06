"""Agent package — LLM client, doctrine, contracts, and orchestrator."""
from app.agent.doctrine import SYSTEM_PROMPT, build_system_prompt
from app.agent.contracts import (
    AGENT_TOOL_NAME,
    AGENT_RESPONSE_SCHEMA,
    build_user_message,
    parse_agent_response,
    history_to_contract_dicts,
    MalformedAgentResponse,
)
from app.agent.llm import BaseLLM, AnthropicLLM, StubLLM, get_llm_client, LLMError

__all__ = [
    "SYSTEM_PROMPT",
    "build_system_prompt",
    "AGENT_TOOL_NAME",
    "AGENT_RESPONSE_SCHEMA",
    "build_user_message",
    "parse_agent_response",
    "history_to_contract_dicts",
    "MalformedAgentResponse",
    "BaseLLM",
    "AnthropicLLM",
    "StubLLM",
    "get_llm_client",
    "LLMError",
]
