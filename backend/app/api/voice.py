"""Voice agent API — proxies ElevenLabs signed-URL generation.

The ElevenLabs API key is a backend secret and must never reach the browser.
This module issues short-lived signed WebSocket URLs on behalf of authenticated
clients so the frontend can connect directly to ElevenLabs without ever seeing
the key.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/voice")


def _settings(request: Request) -> Any:
    return request.app.state.settings


@router.get("/config")
async def get_voice_config(request: Request) -> dict[str, object]:
    """Return feature-flag + agent id so the frontend can decide whether to show the mic.

    The agent_id is not a secret (it travels to the browser during connection
    setup anyway). The api_key is never returned.
    """
    settings = _settings(request)
    return {
        "enabled": settings.voice_configured,
        "agent_id": settings.elevenlabs_agent_id,
    }


@router.get("/signed-url")
async def get_signed_url(request: Request) -> dict[str, str]:
    """Obtain a short-lived ElevenLabs WebSocket signed URL.

    ElevenLabs requires the xi-api-key for this call; the signed URL it returns
    expires in ~15 min and is safe to send to the browser. The api_key is never
    included in any response or log.
    """
    settings = _settings(request)
    if not settings.voice_configured:
        raise HTTPException(status_code=503, detail="Voice agent not configured")

    url = f"{settings.elevenlabs_base_url}/v1/convai/conversation/get-signed-url"
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            resp = await client.get(
                url,
                params={"agent_id": settings.elevenlabs_agent_id},
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail="Failed to get signed URL from ElevenLabs",
        )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="Failed to get signed URL from ElevenLabs",
        )

    try:
        signed_url = resp.json().get("signed_url")
    except Exception:
        signed_url = None

    if not signed_url:
        raise HTTPException(
            status_code=502,
            detail="Failed to get signed URL from ElevenLabs",
        )

    return {"signed_url": signed_url}
