"""Phoenix ERP async HTTP client — SPEC §2.2.

Wraps all 8 ERP endpoints with typed return values, per-call timeouts,
exponential-backoff retries on 5xx/network errors, and typed exceptions for
auth failures and unexpected non-2xx responses.

Usage::

    from app.erp_client import make_erp_client

    async with make_erp_client() as erp:
        me = await erp.get_me()
        tickets = await erp.list_tickets()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Union

import httpx

from app.config import Settings, get_settings
from app.models import (
    Activity,
    ActivityCreate,
    Customer,
    CustomerSystem,
    Employee,
    Ticket,
    TicketStatus,
)

# ---------------------------------------------------------------------------
# Module logger — never log the token or full Authorization header
# ---------------------------------------------------------------------------
logger = logging.getLogger("app.erp_client")


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class ErpError(Exception):
    """Raised when the ERP returns an unexpected non-2xx response (SPEC §2.2)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ErpAuthError(ErpError):
    """Raised on HTTP 401 — token missing or rejected (SPEC §2.2)."""


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)  # delay *before* attempt n (n>=1)


def _is_retryable_status(status_code: int) -> bool:
    return status_code >= 500


# ---------------------------------------------------------------------------
# ErpClient
# ---------------------------------------------------------------------------


class ErpClient:
    """Async client for the Phoenix ERP REST API (SPEC §2.2).

    All requests include ``Authorization: Bearer <token>`` and respect the
    configured ``timeout``.  5xx responses and network errors are retried up
    to 3 times with exponential backoff.  4xx responses are never retried.

    Args:
        base_url: ERP base URL (e.g. ``http://localhost:9000``).
        token: Phoenix API Bearer token — never logged.
        timeout: Per-request timeout in seconds (default 15).
        client: Optional pre-built ``httpx.AsyncClient``.  When supplied the
            caller owns the client lifecycle; ``aclose()`` will NOT close it.
            Useful for injecting ``httpx.ASGITransport`` in tests.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = httpx.Timeout(timeout)
        self._owned = client is None
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ErpClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owned:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal request helper with retry logic
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        """Execute *method* request to *path*, retrying on 5xx/network errors.

        Raises:
            ErpAuthError: on HTTP 401.
            ErpError: on other non-2xx after all retries exhausted.
        """
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(_MAX_ATTEMPTS):
            if attempt > 0:
                delay = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
                logger.debug(
                    "ERP retry %d/%d for %s %s (sleeping %.1fs)",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    method,
                    path,
                    delay,
                )
                await asyncio.sleep(delay)

            try:
                resp = await self._client.request(
                    method,
                    url,
                    headers=self._auth_headers(),
                    params=params,
                    json=json,
                    timeout=self._timeout,
                )
            except (httpx.TransportError, httpx.ConnectError, httpx.ReadTimeout) as exc:
                logger.warning("ERP network error on %s %s: %s", method, path, exc)
                last_exc = exc
                continue

            if resp.status_code == 401:
                raise ErpAuthError(
                    "Phoenix ERP rejected the token (401) — check PHOENIX_API_TOKEN.",
                    status_code=401,
                )

            if _is_retryable_status(resp.status_code):
                logger.warning(
                    "ERP %d on %s %s (attempt %d/%d)",
                    resp.status_code,
                    method,
                    path,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                )
                last_exc = ErpError(
                    f"ERP returned {resp.status_code} for {method} {path}",
                    status_code=resp.status_code,
                )
                continue

            # 4xx (except 401/404 handled by callers) or 2xx — return as-is
            return resp

        # All attempts exhausted
        if last_exc is not None:
            if isinstance(last_exc, ErpError):
                raise last_exc
            raise ErpError(
                f"ERP request {method} {path} failed after {_MAX_ATTEMPTS} attempts: {last_exc}",
            ) from last_exc

        raise ErpError(f"ERP request {method} {path} failed (unknown reason)")

    # ------------------------------------------------------------------
    # Public API methods (SPEC §2.2)
    # ------------------------------------------------------------------

    async def get_me(self) -> Employee:
        """GET /api/v1/me — return the logged-in technician (SPEC §2.2)."""
        resp = await self._request("GET", "/api/v1/me")
        return Employee.model_validate(resp.json())

    async def list_tickets(
        self,
        status: str | None = None,
        priority: str | None = None,
        sort: str = "date",
    ) -> list[Ticket]:
        """GET /api/v1/me/tickets — list assigned tickets (SPEC §2.2).

        Optional ``status``, ``priority``, and ``sort`` query params.  ``None``
        values are omitted from the request.  Returns ``[]`` on an empty list.
        """
        params: dict[str, str] = {"sort": sort}
        if status is not None:
            params["status"] = status
        if priority is not None:
            params["priority"] = priority

        resp = await self._request("GET", "/api/v1/me/tickets", params=params)
        data = resp.json()
        if not data:
            return []
        return [Ticket.model_validate(t) for t in data]

    async def get_ticket(self, ticket_id: int) -> Ticket | None:
        """GET /api/v1/tickets/{id} — return ticket or None on 404 (SPEC §2.2)."""
        resp = await self._request("GET", f"/api/v1/tickets/{ticket_id}")
        if resp.status_code == 404:
            logger.debug("Ticket %d not found (404)", ticket_id)
            return None
        if not resp.is_success:
            raise ErpError(
                f"ERP returned {resp.status_code} for GET /api/v1/tickets/{ticket_id}",
                status_code=resp.status_code,
            )
        return Ticket.model_validate(resp.json())

    async def get_customer_system(self, ticket_id: int) -> CustomerSystem | None:
        """GET /api/v1/tickets/{id}/customer-system — None on 404 (SPEC §2.2)."""
        resp = await self._request(
            "GET", f"/api/v1/tickets/{ticket_id}/customer-system"
        )
        if resp.status_code == 404:
            logger.debug("CustomerSystem for ticket %d not found (404)", ticket_id)
            return None
        if not resp.is_success:
            raise ErpError(
                f"ERP returned {resp.status_code} for GET /api/v1/tickets/{ticket_id}/customer-system",
                status_code=resp.status_code,
            )
        return CustomerSystem.model_validate(resp.json())

    async def get_customer(self, customer_id: int) -> Customer | None:
        """GET /api/v1/customers/{id} — return customer or None on 404 (SPEC §2.2)."""
        resp = await self._request("GET", f"/api/v1/customers/{customer_id}")
        if resp.status_code == 404:
            logger.debug("Customer %d not found (404)", customer_id)
            return None
        if not resp.is_success:
            raise ErpError(
                f"ERP returned {resp.status_code} for GET /api/v1/customers/{customer_id}",
                status_code=resp.status_code,
            )
        return Customer.model_validate(resp.json())

    async def set_status(
        self,
        ticket_id: int,
        status: Union[TicketStatus, str],
    ) -> Ticket | None:
        """PATCH /api/v1/tickets/{id}/status — update ticket status (SPEC §2.2).

        Returns the updated ``Ticket``, or ``None`` if the ticket was not found.
        """
        status_value = status.value if isinstance(status, TicketStatus) else str(status)
        resp = await self._request(
            "PATCH",
            f"/api/v1/tickets/{ticket_id}/status",
            json={"status": status_value},
        )
        if resp.status_code == 404:
            logger.debug("Ticket %d not found for set_status (404)", ticket_id)
            return None
        if not resp.is_success:
            raise ErpError(
                f"ERP returned {resp.status_code} for PATCH /api/v1/tickets/{ticket_id}/status",
                status_code=resp.status_code,
            )
        return Ticket.model_validate(resp.json())

    async def create_activity(self, activity: ActivityCreate) -> Activity:
        """POST /api/v1/activities/create — write activity to ERP (SPEC §2.2).

        Returns the created ``Activity`` (HTTP 201).
        """
        resp = await self._request(
            "POST",
            "/api/v1/activities/create",
            json=activity.model_dump(),
        )
        if not resp.is_success:
            raise ErpError(
                f"ERP returned {resp.status_code} for POST /api/v1/activities/create",
                status_code=resp.status_code,
            )
        return Activity.model_validate(resp.json())

    async def reset(self) -> dict:
        """POST /api/v1/me/reset — clear activities and reboot VMs (SPEC §2.2).

        Returns the parsed JSON message dict from the ERP.
        """
        resp = await self._request("POST", "/api/v1/me/reset")
        if not resp.is_success:
            raise ErpError(
                f"ERP returned {resp.status_code} for POST /api/v1/me/reset",
                status_code=resp.status_code,
            )
        return resp.json()


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------


def make_erp_client(settings: Settings | None = None) -> ErpClient:
    """Create an ``ErpClient`` from application settings (SPEC §2.2).

    If *settings* is ``None``, ``get_settings()`` is called automatically.
    """
    if settings is None:
        settings = get_settings()
    return ErpClient(
        base_url=settings.phoenix_api_base_url,
        token=settings.phoenix_api_token,
    )
