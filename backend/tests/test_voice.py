"""Tests for the voice-agent endpoints.

These verify the ElevenLabs signed-URL proxy: that the config endpoint always
returns 200, that the signed-url endpoint returns 503 when unconfigured, and that
it proxies correctly (with the right header) when configured — without ever
leaking the api key into responses.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


@pytest.fixture
def unconfigured_client():
    """TestClient with voice agent settings empty (default)."""
    with TestClient(app) as c:
        app.state.settings = Settings(
            elevenlabs_api_key="",
            elevenlabs_agent_id="",
        )
        yield c


@pytest.fixture
def configured_client():
    """TestClient with voice agent settings populated."""
    with TestClient(app) as c:
        app.state.settings = Settings(
            elevenlabs_api_key="test-secret-key",
            elevenlabs_agent_id="agent-abc123",
        )
        yield c


# --------------------------------------------------------------------------- #
# GET /api/voice/config
# --------------------------------------------------------------------------- #

def test_voice_config_unconfigured(unconfigured_client):
    resp = unconfigured_client.get("/api/voice/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"enabled": False, "agent_id": ""}


def test_voice_config_configured(configured_client):
    resp = configured_client.get("/api/voice/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["agent_id"] == "agent-abc123"


# --------------------------------------------------------------------------- #
# GET /api/voice/signed-url
# --------------------------------------------------------------------------- #

def test_signed_url_503_when_unconfigured(unconfigured_client):
    resp = unconfigured_client.get("/api/voice/signed-url")
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


def test_signed_url_proxies_elevenlabs(configured_client, monkeypatch):
    """Happy path: monkeypatched httpx returns a signed URL; api key must not appear in response."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"signed_url": "wss://signed.example"}

    captured_headers: dict = {}

    async def fake_get(self, url, *, params=None, headers=None, **kwargs):
        captured_headers.update(headers or {})
        return fake_response

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    resp = configured_client.get("/api/voice/signed-url")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"signed_url": "wss://signed.example"}

    # The api key must have been sent to ElevenLabs…
    assert captured_headers.get("xi-api-key") == "test-secret-key"
    # …but must NOT appear anywhere in the response body.
    assert "test-secret-key" not in resp.text


def test_signed_url_502_on_elevenlabs_error(configured_client, monkeypatch):
    """Upstream 4xx/5xx must map to a 502 without leaking upstream body or api key."""
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.json.return_value = {"detail": "invalid xi-api-key test-secret-key"}

    async def fake_get(self, url, *, params=None, headers=None, **kwargs):
        return fake_response

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    resp = configured_client.get("/api/voice/signed-url")
    assert resp.status_code == 502
    assert "test-secret-key" not in resp.text


def test_signed_url_502_on_httpx_error(configured_client, monkeypatch):
    """Network-level failures must also produce a clean 502."""
    import httpx as _httpx

    async def fake_get(self, url, *, params=None, headers=None, **kwargs):
        raise _httpx.ConnectError("unreachable")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    resp = configured_client.get("/api/voice/signed-url")
    assert resp.status_code == 502
    assert "test-secret-key" not in resp.text


def test_signed_url_502_on_missing_signed_url_field(configured_client, monkeypatch):
    """Upstream returns 200 with a body lacking 'signed_url' → endpoint returns 502."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"unexpected_field": "some_value"}

    async def fake_get(self, url, *, params=None, headers=None, **kwargs):
        return fake_response

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    resp = configured_client.get("/api/voice/signed-url")
    assert resp.status_code == 502
    assert "test-secret-key" not in resp.text
