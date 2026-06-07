"""FastAPI entrypoint for the AI Service Desk Autopilot backend.

Wires the Phoenix ERP client, the OpenRouter LLM, and the run manager into the
app state, and mounts the REST + WebSocket API. The ERP token and SSH key stay
on the backend and are never exposed to the browser.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.llm import LLM
from app.api.routes import router as api_router
from app.api.voice import router as voice_router
from app.api.ws import router as ws_router
from app.config import get_settings
from app.erp import PhoenixClient
from app.runs import RunManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.erp = PhoenixClient(
        base_url=settings.phoenix_api_base_url,
        token=settings.phoenix_api_token,
        timeout=settings.request_timeout,
    )
    app.state.llm = LLM(settings)
    manager = RunManager(settings)
    manager.erp = app.state.erp
    app.state.manager = manager
    try:
        yield
    finally:
        await manager.shutdown()
        await app.state.erp.aclose()


app = FastAPI(
    title="techbold AI Service Desk Autopilot — Team Backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "phoenix_configured": bool(settings.phoenix_api_token),
        "llm_configured": settings.llm_configured,
        "voice_configured": settings.voice_configured,
    }


app.include_router(api_router)
app.include_router(voice_router)
app.include_router(ws_router)
