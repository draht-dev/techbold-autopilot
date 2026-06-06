"""Provider-abstracted LLM client — SPEC §1, §7.2.

The orchestrator calls only ``BaseLLM.propose()``.  All provider-specific
details (SDK, auth, tool-calling shape) are hidden inside the concrete
subclasses.  Swap providers via ``LLM_PROVIDER`` in the environment without
touching any agent logic.

Provider support
----------------
- ``AnthropicLLM``   — default; uses ``anthropic.AsyncAnthropic``.
- ``OpenAILLM``      — ``openai.AsyncOpenAI``; imported lazily so the package
                       is optional.
- ``AzureOpenAILLM`` — thin variant of OpenAILLM using ``AsyncAzureOpenAI``;
                       lazy import.
- ``StubLLM``        — deterministic replay for tests / offline full-loop.

Errors
------
- ``LLMError``              — network / API / timeout failures (SPEC §13).
- ``MalformedAgentResponse``— model returned invalid JSON; see contracts.py.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, Union

from app.agent.contracts import (
    AGENT_RESPONSE_SCHEMA,
    AGENT_TOOL_NAME,
    MalformedAgentResponse,
    parse_agent_response,
)
from app.models import AgentResponse

logger = logging.getLogger("app.agent.llm")

# Maximum tokens the model may emit per call.
_DEFAULT_MAX_TOKENS = 4096

# Responses API budget — reasoning models (e.g. gpt-5.x) spend output tokens on
# internal reasoning, so give the call generous headroom for thinking + JSON.
_DEFAULT_RESPONSES_MAX_TOKENS = 16384

# Number of automatic retries on malformed / missing tool-use responses.
_MAX_RETRIES = 2


async def _openai_chat_create(client: Any, **kwargs: Any) -> Any:
    """Call chat.completions.create with a token-parameter fallback.

    Classic chat models accept ``max_tokens``; newer reasoning models (o1/o3 and
    some gpt-4.x variants) reject it and require ``max_completion_tokens``.  Try
    the former, and on the specific "unsupported parameter" error retry with the
    latter so the same code works across Azure/OpenAI deployments.
    """
    try:
        return await client.chat.completions.create(
            max_tokens=_DEFAULT_MAX_TOKENS, **kwargs
        )
    except Exception as exc:  # noqa: BLE001 — inspect message, then re-raise if unrelated
        msg = str(exc).lower()
        if "max_completion_tokens" in msg or (
            "max_tokens" in msg and ("unsupported" in msg or "not supported" in msg)
        ):
            logger.debug("Retrying chat completion with max_completion_tokens.")
            return await client.chat.completions.create(
                max_completion_tokens=_DEFAULT_MAX_TOKENS, **kwargs
            )
        raise


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Raised on network, API, or timeout errors from any LLM provider."""

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"[{provider}] {detail}")
        self.provider = provider
        self.detail = detail


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseLLM(ABC):
    """Contract the orchestrator uses.  Providers must implement ``propose``."""

    @abstractmethod
    async def propose(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> AgentResponse:
        """Send the doctrine + user context to the model; return a parsed response.

        This is the ONLY method the orchestrator calls — all provider details
        (SDK, auth, serialisation) are encapsulated here.

        Raises
        ------
        LLMError
            On network / API / timeout failures.
        MalformedAgentResponse
            If the model does not return valid structured output after retries.
        """


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicLLM(BaseLLM):
    """LLM client backed by ``anthropic.AsyncAnthropic`` (default provider).

    Uses the Anthropic tool-use API to force structured JSON output that
    matches AGENT_RESPONSE_SCHEMA.  On malformed / missing tool-use blocks the
    call is retried up to ``_MAX_RETRIES`` times with a corrective note before
    raising ``MalformedAgentResponse``.
    """

    _PROVIDER = "anthropic"

    def __init__(self, *, api_key: str, model: str) -> None:
        import anthropic  # always installed; top-level import would be fine too

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def propose(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> AgentResponse:
        import anthropic

        messages: list[dict[str, str]] = [{"role": "user", "content": user_message}]
        last_error: MalformedAgentResponse | None = None

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0 and last_error is not None:
                # Append a corrective note so the model can self-correct.
                correction = (
                    f"\n\n[System correction — attempt {attempt + 1}]: "
                    f"Your previous response was invalid. {last_error.detail} "
                    f"You MUST call the `{AGENT_TOOL_NAME}` tool with all required fields."
                )
                corrected_user = user_message + correction
                messages = [{"role": "user", "content": corrected_user}]
                logger.debug(
                    "Retrying Anthropic call (attempt %d/%d) after malformed response.",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )

            try:
                response = await self._client.messages.create(
                    model=self._model,
                    system=system_prompt,
                    messages=messages,
                    tools=[
                        {
                            "name": AGENT_TOOL_NAME,
                            "description": (
                                "Report the agent's structured reasoning and proposed commands."
                            ),
                            "input_schema": AGENT_RESPONSE_SCHEMA,
                        }
                    ],
                    tool_choice={"type": "tool", "name": AGENT_TOOL_NAME},
                    max_tokens=_DEFAULT_MAX_TOKENS,
                )
            except anthropic.APITimeoutError as exc:
                raise LLMError(self._PROVIDER, f"Request timed out: {exc}") from exc
            except anthropic.APIConnectionError as exc:
                raise LLMError(
                    self._PROVIDER, f"Connection error: {exc}"
                ) from exc
            except anthropic.APIStatusError as exc:
                raise LLMError(
                    self._PROVIDER,
                    f"API error {exc.status_code}: {exc.message}",
                ) from exc
            except anthropic.AnthropicError as exc:
                raise LLMError(self._PROVIDER, str(exc)) from exc

            # Extract the tool_use block from the response.
            tool_block = next(
                (
                    block
                    for block in response.content
                    if block.type == "tool_use" and block.name == AGENT_TOOL_NAME
                ),
                None,
            )

            if tool_block is None:
                last_error = MalformedAgentResponse(
                    f"No `{AGENT_TOOL_NAME}` tool_use block found in the response. "
                    "The model must call the tool — free-form text is not accepted."
                )
                continue

            try:
                return parse_agent_response(tool_block.input)
            except MalformedAgentResponse as exc:
                last_error = exc
                continue

        # All retries exhausted.
        assert last_error is not None
        raise last_error


# ---------------------------------------------------------------------------
# OpenAI (lazy import — package may not be installed)
# ---------------------------------------------------------------------------


class OpenAILLM(BaseLLM):
    """LLM client backed by ``openai.AsyncOpenAI``.

    The ``openai`` package is imported lazily so that importing this module
    never fails when only the ``anthropic`` package is present.
    """

    _PROVIDER = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def _get_client(self) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMError(
                self._PROVIDER,
                "The `openai` package is not installed. "
                "Run `pip install openai` or switch to LLM_PROVIDER=anthropic.",
            ) from exc
        return AsyncOpenAI(api_key=self._api_key)

    async def propose(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> AgentResponse:
        client = self._get_client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        last_error: MalformedAgentResponse | None = None

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0 and last_error is not None:
                correction = (
                    f"\n\n[System correction — attempt {attempt + 1}]: "
                    f"Your previous response was invalid. {last_error.detail} "
                    f"You MUST call the `{AGENT_TOOL_NAME}` function with all required fields."
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message + correction},
                ]
                logger.debug(
                    "Retrying OpenAI call (attempt %d/%d) after malformed response.",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )

            try:
                response = await _openai_chat_create(
                    client,
                    model=self._model,
                    messages=messages,
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": AGENT_TOOL_NAME,
                                "description": (
                                    "Report the agent's structured reasoning and proposed commands."
                                ),
                                "parameters": AGENT_RESPONSE_SCHEMA,
                            },
                        }
                    ],
                    tool_choice={
                        "type": "function",
                        "function": {"name": AGENT_TOOL_NAME},
                    },
                )
            except Exception as exc:
                # Lazy import means we can't catch openai-specific types directly.
                raise LLMError(self._PROVIDER, str(exc)) from exc

            choice = response.choices[0] if response.choices else None
            tool_calls = (
                choice.message.tool_calls if choice and choice.message else None
            )

            if not tool_calls:
                last_error = MalformedAgentResponse(
                    f"No `{AGENT_TOOL_NAME}` tool call found in the response."
                )
                continue

            tool_call = next(
                (tc for tc in tool_calls if tc.function.name == AGENT_TOOL_NAME),
                None,
            )
            if tool_call is None:
                last_error = MalformedAgentResponse(
                    f"No `{AGENT_TOOL_NAME}` tool call found in the response."
                )
                continue

            import json

            try:
                raw = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as exc:
                last_error = MalformedAgentResponse(
                    f"Tool call arguments are not valid JSON: {exc}"
                )
                continue

            try:
                return parse_agent_response(raw)
            except MalformedAgentResponse as exc:
                last_error = exc
                continue

        assert last_error is not None
        raise last_error


# ---------------------------------------------------------------------------
# Azure OpenAI (lazy import — thin variant of OpenAILLM)
# ---------------------------------------------------------------------------


class AzureOpenAILLM(BaseLLM):
    """LLM client backed by ``openai.AsyncAzureOpenAI``.

    Configures the Azure-specific endpoint + deployment from settings.
    The ``openai`` package is imported lazily.
    """

    _PROVIDER = "azure-openai"

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-02-15-preview",
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._deployment = deployment
        self._api_version = api_version

    def _get_client(self) -> Any:
        try:
            from openai import AsyncAzureOpenAI
        except ImportError as exc:
            raise LLMError(
                self._PROVIDER,
                "The `openai` package is not installed. "
                "Run `pip install openai` or switch to LLM_PROVIDER=anthropic.",
            ) from exc
        return AsyncAzureOpenAI(
            api_key=self._api_key,
            azure_endpoint=self._endpoint,
            azure_deployment=self._deployment,
            api_version=self._api_version,
        )

    async def propose(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> AgentResponse:
        # Delegate to a temporary OpenAI-compatible instance that uses our client.
        client = self._get_client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        last_error: MalformedAgentResponse | None = None

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0 and last_error is not None:
                correction = (
                    f"\n\n[System correction — attempt {attempt + 1}]: "
                    f"Your previous response was invalid. {last_error.detail} "
                    f"You MUST call the `{AGENT_TOOL_NAME}` function with all required fields."
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message + correction},
                ]
                logger.debug(
                    "Retrying Azure OpenAI call (attempt %d/%d) after malformed response.",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )

            try:
                response = await _openai_chat_create(
                    client,
                    model=self._deployment,
                    messages=messages,
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": AGENT_TOOL_NAME,
                                "description": (
                                    "Report the agent's structured reasoning and proposed commands."
                                ),
                                "parameters": AGENT_RESPONSE_SCHEMA,
                            },
                        }
                    ],
                    tool_choice={
                        "type": "function",
                        "function": {"name": AGENT_TOOL_NAME},
                    },
                )
            except Exception as exc:
                raise LLMError(self._PROVIDER, str(exc)) from exc

            choice = response.choices[0] if response.choices else None
            tool_calls = (
                choice.message.tool_calls if choice and choice.message else None
            )

            if not tool_calls:
                last_error = MalformedAgentResponse(
                    f"No `{AGENT_TOOL_NAME}` tool call found in the response."
                )
                continue

            tool_call = next(
                (tc for tc in tool_calls if tc.function.name == AGENT_TOOL_NAME),
                None,
            )
            if tool_call is None:
                last_error = MalformedAgentResponse(
                    f"No `{AGENT_TOOL_NAME}` tool call found in the response."
                )
                continue

            import json

            try:
                raw = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as exc:
                last_error = MalformedAgentResponse(
                    f"Tool call arguments are not valid JSON: {exc}"
                )
                continue

            try:
                return parse_agent_response(raw)
            except MalformedAgentResponse as exc:
                last_error = exc
                continue

        assert last_error is not None
        raise last_error


# ---------------------------------------------------------------------------
# Azure AI Foundry — v1 "Responses" API (lazy import)
# ---------------------------------------------------------------------------


class AzureFoundryLLM(BaseLLM):
    """LLM client for the Azure AI Foundry **v1 Responses** API.

    This is the surface techbold provides, e.g.::

        POST https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/responses
        header: api-key: <key>
        body:   {"model": "gpt-5.4-nano", "input": "..."}

    It differs from classic Azure OpenAI (``AzureOpenAILLM``): the model name
    lives in the body (no deployment in the URL), auth is the ``api-key`` header,
    and the call uses ``responses.create`` (``input``/``instructions``) rather
    than ``chat.completions``.  Structured output is forced with a json_schema
    ``text.format`` so the model returns AGENT_RESPONSE_SCHEMA-shaped JSON.

    ``base_url`` must be the part up to and including ``/openai/v1`` (the SDK
    appends ``/responses``).  Works for any OpenAI-compatible ``/v1/responses``
    endpoint, not just Azure.
    """

    _PROVIDER = "azure-foundry"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: Any | None = None,
        max_output_tokens: int = _DEFAULT_RESPONSES_MAX_TOKENS,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client  # injectable for tests
        self._max_output_tokens = max_output_tokens

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMError(
                self._PROVIDER,
                "The `openai` package is not installed. Run `pip install openai`.",
            ) from exc
        # api-key header matches the techbold curl; api_key= also sets the
        # Authorization: Bearer header, so the endpoint accepts either scheme.
        return AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            default_headers={"api-key": self._api_key},
        )

    async def propose(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> AgentResponse:
        import json

        client = self._get_client()
        text_format = {
            "format": {
                "type": "json_schema",
                "name": AGENT_TOOL_NAME,
                "schema": AGENT_RESPONSE_SCHEMA,
                # Non-strict: our schema has optional fields; pydantic + the
                # retry loop below validate the result. The schema still guides
                # the model strongly toward the right shape.
                "strict": False,
            }
        }
        last_error: MalformedAgentResponse | None = None
        user = user_message

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0 and last_error is not None:
                user = user_message + (
                    f"\n\n[System correction — attempt {attempt + 1}]: "
                    f"Your previous response was invalid. {last_error.detail} "
                    "Respond ONLY with JSON matching the required schema."
                )
                logger.debug(
                    "Retrying Azure Foundry call (attempt %d/%d) after malformed response.",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )

            try:
                response = await client.responses.create(
                    model=self._model,
                    instructions=system_prompt,
                    input=user,
                    text=text_format,
                    max_output_tokens=self._max_output_tokens,
                )
            except Exception as exc:  # lazy import — can't catch openai types here
                raise LLMError(self._PROVIDER, str(exc)) from exc

            raw_text = getattr(response, "output_text", None)
            if not raw_text:
                last_error = MalformedAgentResponse(
                    "The model returned no output text (possibly truncated or a "
                    "refusal). It must return JSON matching the schema."
                )
                continue

            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                last_error = MalformedAgentResponse(
                    f"Model output was not valid JSON: {exc}"
                )
                continue

            try:
                return parse_agent_response(raw)
            except MalformedAgentResponse as exc:
                last_error = exc
                continue

        assert last_error is not None
        raise last_error


# ---------------------------------------------------------------------------
# Stub (for tests / offline full-loop)
# ---------------------------------------------------------------------------


class StubLLM(BaseLLM):
    """Deterministic replay LLM for tests and offline full-loop runs.

    Accepts a list of ``AgentResponse`` objects (or dicts) and returns them in
    sequence on successive ``propose()`` calls.  When the list is exhausted the
    last response is repeated (or a safe no-op finish is synthesised).

    Example
    -------
    ::

        stub = StubLLM([response_a, response_b])
        result = await stub.propose(system_prompt="s", user_message="u")
        # result == response_a
    """

    def __init__(
        self,
        responses: list[Union[AgentResponse, dict[str, Any]]],
    ) -> None:
        parsed: list[AgentResponse] = []
        for r in responses:
            if isinstance(r, AgentResponse):
                parsed.append(r)
            else:
                parsed.append(parse_agent_response(r))
        self._responses = parsed
        self._index = 0

    def _next(self) -> AgentResponse:
        if not self._responses:
            # Synthesise a safe finish if no responses were provided.
            return AgentResponse(
                phase="diagnose",
                thought="No stub responses configured.",
                hypotheses=[],
                proposed_commands=[],
                ready_to_validate=False,
                ready_to_finish=False,
                final=None,
            )
        response = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return response

    async def propose(
        self,
        *,
        system_prompt: str,  # noqa: ARG002
        user_message: str,  # noqa: ARG002
    ) -> AgentResponse:
        return self._next()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_llm_client(settings: Any | None = None) -> BaseLLM:
    """Return the configured LLM client, switching on ``LLM_PROVIDER``.

    The orchestrator calls this once at session start.  Providers are selected
    via the ``LLM_PROVIDER`` environment variable (set in ``config.py``).

    Raises
    ------
    LLMError
        If the selected provider's required credentials are missing.
    ValueError
        If ``LLM_PROVIDER`` names an unknown provider.
    """
    from app.config import get_settings

    if settings is None:
        settings = get_settings()

    provider = (settings.llm_provider or "anthropic").lower().strip()

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError(
                "anthropic",
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file or environment.",
            )
        model = settings.llm_model or "claude-opus-4-8"
        logger.info("Using Anthropic LLM provider, model=%s", model)
        return AnthropicLLM(api_key=settings.anthropic_api_key, model=model)

    if provider == "openai":
        if not settings.openai_api_key:
            raise LLMError(
                "openai",
                "OPENAI_API_KEY is not set. "
                "Add it to your .env file or environment.",
            )
        model = settings.llm_model or "gpt-4o"
        logger.info("Using OpenAI LLM provider, model=%s", model)
        return OpenAILLM(api_key=settings.openai_api_key, model=model)

    if provider == "azure-openai":
        missing: list[str] = []
        if not settings.azure_openai_api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if not settings.azure_openai_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not settings.azure_openai_deployment:
            missing.append("AZURE_OPENAI_DEPLOYMENT")
        if missing:
            raise LLMError(
                "azure-openai",
                f"Missing required settings: {', '.join(missing)}. "
                "Add them to your .env file or environment.",
            )
        api_version = settings.azure_openai_api_version or "2024-10-21"
        logger.info(
            "Using Azure OpenAI LLM provider, deployment=%s api_version=%s",
            settings.azure_openai_deployment,
            api_version,
        )
        return AzureOpenAILLM(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_deployment,
            api_version=api_version,
        )

    if provider in ("azure-foundry", "azure-ai-foundry", "azure-responses", "foundry"):
        missing = []
        if not settings.azure_openai_api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if not settings.azure_openai_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT (the full .../openai/v1 base URL)")
        model = settings.llm_model or settings.azure_openai_deployment
        if not model:
            missing.append("LLM_MODEL (the model name, e.g. gpt-5.4-nano)")
        if missing:
            raise LLMError(
                "azure-foundry",
                f"Missing required settings: {', '.join(missing)}. "
                "Add them to your .env file or environment.",
            )
        logger.info(
            "Using Azure AI Foundry (Responses API) provider, model=%s", model
        )
        return AzureFoundryLLM(
            api_key=settings.azure_openai_api_key,
            base_url=settings.azure_openai_endpoint,
            model=model,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. Supported values: "
        "anthropic, openai, azure-openai, azure-foundry."
    )
