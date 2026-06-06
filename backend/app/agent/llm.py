"""LangChain-based OpenRouter client.

Uses ``ChatOpenAI`` (OpenAI-compatible) with ``with_structured_output`` and
Pydantic schemas so we do not depend on fragile hand-parsed JSON. Falls back
through json_mode and a ``raw_decode`` extractor when a provider misbehaves.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Type

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import Settings


class LLMError(Exception):
    pass


def _extract_first_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object from model text (handles trailing junk)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        if text[idx] not in "{[":
            idx += 1
            continue
        try:
            obj, _end = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx += 1

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            obj, _end = decoder.raw_decode(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError as exc:
            raise LLMError(f"Could not parse JSON from model: {exc}") from exc
    raise LLMError("Model did not return JSON")


class LLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.agent_model = settings.agent_model
        self.fast_model = settings.fast_model
        self.configured = settings.llm_configured

    def _chat(self, model: str, temperature: float) -> ChatOpenAI:
        if not self.configured:
            raise LLMError("OpenRouter API key not configured (set OPENROUTER_API_KEY)")
        return ChatOpenAI(
            model=model,
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
            temperature=temperature,
            default_headers={
                "HTTP-Referer": "https://github.com/start-hack-vienna",
                "X-Title": "Service Desk Autopilot",
            },
        )

    async def complete_json(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        schema: Optional[Type[BaseModel]] = None,
    ) -> dict[str, Any]:
        model = model or self.agent_model
        chat = self._chat(model, temperature)
        messages = [SystemMessage(content=system), HumanMessage(content=user)]

        if schema is not None:
            for method in ("json_schema", "function_calling", "json_mode"):
                try:
                    structured = chat.with_structured_output(schema, method=method)
                    result = await structured.ainvoke(messages)
                    if isinstance(result, BaseModel):
                        return result.model_dump()
                    if isinstance(result, dict):
                        return result
                except Exception:
                    continue

        # Last resort: plain completion + first-object JSON extraction.
        try:
            response = await chat.bind(response_format={"type": "json_object"}).ainvoke(messages)
        except Exception:
            response = await chat.ainvoke(messages)

        content = response.content
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMError("Model returned empty content")
        parsed = _extract_first_json_object(content)
        if schema is not None:
            return schema.model_validate(parsed).model_dump()
        return parsed

    async def complete_text(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        chat = self._chat(model or self.agent_model, temperature)
        response = await chat.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return str(content or "")
