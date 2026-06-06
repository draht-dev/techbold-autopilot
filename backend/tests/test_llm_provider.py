"""Provider-selection tests for the LLM factory (SPEC §1 — swappable via config).

No live API calls — these assert the factory wires the right client, honours
Azure's api_version/deployment, and fails clearly on missing credentials.
"""
from __future__ import annotations

import pytest

import json

from app.config import Settings
from app.agent.llm import (
    AnthropicLLM,
    AzureFoundryLLM,
    AzureOpenAILLM,
    LLMError,
    OpenAILLM,
    get_llm_client,
)

_VALID_RESPONSE = {
    "phase": "diagnose",
    "thought": "checking nginx",
    "hypotheses": [],
    "proposed_commands": [
        {"command": "systemctl status nginx", "purpose": "p",
         "mutating": False, "expected_effect": "", "rollback": ""}
    ],
    "ready_to_validate": False,
    "ready_to_finish": False,
    "final": None,
}


class _FakeResponses:
    """Stand-in for ``client.responses`` capturing the request + scripting output."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self._outputs.pop(0) if self._outputs else ""

        class _R:
            output_text = out

        return _R()


class _FakeClient:
    def __init__(self, outputs):
        self.responses = _FakeResponses(outputs)


def test_anthropic_is_default_provider():
    c = get_llm_client(Settings(llm_provider="anthropic", anthropic_api_key="k"))
    assert isinstance(c, AnthropicLLM)


def test_openai_provider_selected():
    c = get_llm_client(Settings(llm_provider="openai", openai_api_key="k"))
    assert isinstance(c, OpenAILLM)


def test_azure_provider_uses_endpoint_deployment_and_api_version():
    s = Settings(
        llm_provider="azure-openai",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_deployment="gpt-4o",
        azure_openai_api_version="2024-08-01-preview",
    )
    c = get_llm_client(s)
    assert isinstance(c, AzureOpenAILLM)
    assert c._deployment == "gpt-4o"
    assert c._api_version == "2024-08-01-preview"
    assert c._endpoint == "https://example.openai.azure.com/"
    # The Azure SDK client must construct with these params.
    assert c._get_client() is not None


def test_azure_default_api_version_when_unset():
    s = Settings(
        llm_provider="azure-openai",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_deployment="gpt-4o",
    )
    c = get_llm_client(s)
    assert c._api_version  # falls back to a sane default, never empty


def test_azure_missing_credentials_raises_clear_error():
    with pytest.raises(LLMError) as exc:
        get_llm_client(Settings(llm_provider="azure-openai"))
    msg = str(exc.value)
    assert "AZURE_OPENAI_API_KEY" in msg
    assert "AZURE_OPENAI_ENDPOINT" in msg
    assert "AZURE_OPENAI_DEPLOYMENT" in msg


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_llm_client(Settings(llm_provider="nope"))


# --- Azure AI Foundry (Responses API) -------------------------------------


def test_foundry_provider_selected_and_validates():
    s = Settings(
        llm_provider="azure-foundry",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://r.services.ai.azure.com/api/projects/p/openai/v1/",
        llm_model="gpt-5.4-nano",
    )
    c = get_llm_client(s)
    assert isinstance(c, AzureFoundryLLM)
    assert c._model == "gpt-5.4-nano"
    # trailing slash stripped so the SDK appends /responses cleanly
    assert c._base_url.endswith("/openai/v1")


def test_foundry_missing_settings_raises_clear_error():
    with pytest.raises(LLMError) as exc:
        get_llm_client(Settings(llm_provider="azure-foundry"))
    msg = str(exc.value)
    assert "AZURE_OPENAI_API_KEY" in msg
    assert "AZURE_OPENAI_ENDPOINT" in msg
    assert "LLM_MODEL" in msg


async def test_foundry_responses_request_shape_and_parse():
    fake = _FakeClient([json.dumps(_VALID_RESPONSE)])
    llm = AzureFoundryLLM(
        api_key="k",
        base_url="https://r.services.ai.azure.com/api/projects/p/openai/v1/",
        model="gpt-5.4-nano",
        client=fake,
    )
    result = await llm.propose(system_prompt="SYS", user_message="USER")
    assert result.phase == "diagnose"
    assert result.proposed_commands[0].command == "systemctl status nginx"
    call = fake.responses.calls[0]
    assert call["model"] == "gpt-5.4-nano"
    assert call["instructions"] == "SYS"          # system prompt -> instructions
    assert call["input"] == "USER"                # user message -> input (Responses API)
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["name"] == "report"
    assert "max_output_tokens" in call            # not max_tokens


async def test_foundry_retries_on_empty_then_valid():
    fake = _FakeClient(["", json.dumps(_VALID_RESPONSE)])  # empty first, valid second
    llm = AzureFoundryLLM(api_key="k", base_url="https://x/openai/v1", model="m", client=fake)
    result = await llm.propose(system_prompt="s", user_message="u")
    assert result.phase == "diagnose"
    assert len(fake.responses.calls) == 2          # it retried after the empty output
