"""techbold AI Service Desk Autopilot — Backend entrypoint.

This module wires together the FastAPI application:
- CORS middleware (open for local dev)
- Health check
- ERP-proxy ticket routes (tickets router)
- Agent session management routes (agent router)
- Redacting log filter on startup so all app logs are secret-free (SPEC §4.3)

The agent loop runs as an async task per session (SPEC §8) and communicates
with the frontend via Server-Sent Events (SPEC §9).  Human approval gates are
driven by POST endpoints that resolve asyncio Futures in the running Session.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import agent, tickets
from app.safety.redaction import make_redacting_log_filter

# ---------------------------------------------------------------------------
# Logging — configure before creating the app so startup messages are redacted
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Configure root logger and attach the redacting filter to all handlers.

    FIX 2: Filters are attached to LOGGER objects (not just their current handlers)
    so that handlers added later by uvicorn (e.g. uvicorn.access) are also covered.
    Handler-level attachment is kept as belt-and-suspenders.
    """
    logging.basicConfig(level=logging.INFO)

    redacting_filter = make_redacting_log_filter()

    # --- Attach to logger objects (covers handlers added at any time) ---
    for logger_name in (
        "",               # root
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "app",            # this app's own namespace
        "fastapi",
    ):
        log = logging.getLogger(logger_name)
        if not any(isinstance(f, type(redacting_filter)) for f in log.filters):
            log.addFilter(redacting_filter)

    # --- Belt-and-suspenders: also attach to any handlers already present ---
    for logger_name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "app", "fastapi"):
        log = logging.getLogger(logger_name)
        for handler in log.handlers:
            if not any(isinstance(f, type(redacting_filter)) for f in handler.filters):
                handler.addFilter(redacting_filter)


_configure_logging()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="techbold AI Service Desk Autopilot — Team Backend",
    description=(
        "Backend for the AI-powered IT service desk autopilot. "
        "Drives a supervised agent that diagnoses and fixes Linux service issues "
        "on customer VMs, with full human approval gating and an audit trail."
    ),
    version="1.0.0",
)

# FIX 1 (security): allow_origins=["*"] with allow_credentials=True is an invalid
# combination that browsers reject, and it is over-permissive given that the ERP
# token lives server-side and is never sent via cookies.  The frontend uses a
# bearer token pattern (no cookies), so credentials are not needed cross-origin.
# Using allow_credentials=False + allow_origins=["*"] is the valid, correct combo
# for a local-dev / demo setup.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    """Liveness probe — always returns 200 OK."""
    return {"status": "ok"}


app.include_router(tickets.router)
app.include_router(agent.router)
