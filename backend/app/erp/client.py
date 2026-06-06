"""Phoenix ERP REST client (async, httpx).

Wraps the endpoints in docs/phoenix-openapi.yaml with bearer auth, timeouts and a
small retry on transient network/5xx errors. Raises PhoenixError with a clear
message on auth/404/validation failures so the API layer can surface them.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.models import (
    Activity,
    ActivityCreate,
    Customer,
    CustomerSystem,
    Employee,
    Ticket,
    TicketStatus,
)


class PhoenixError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PhoenixClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 20.0,
        max_retries: int = 2,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PhoenixClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise PhoenixError(f"Phoenix unreachable: {exc}") from exc

            if resp.status_code >= 500 and attempt < self._max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return resp
        raise PhoenixError(f"Phoenix request failed: {last_exc}")

    @staticmethod
    def _check(resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise PhoenixError("Unauthorized: check PHOENIX_API_TOKEN", 401)
        if resp.status_code == 404:
            raise PhoenixError("Not found", 404)
        if resp.status_code == 422:
            raise PhoenixError(f"Validation error: {resp.text}", 422)
        if resp.status_code >= 400:
            raise PhoenixError(f"Phoenix error {resp.status_code}: {resp.text}", resp.status_code)
        try:
            return resp.json()
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    async def get_me(self) -> Employee:
        data = self._check(await self._request("GET", "/api/v1/me"))
        return Employee.model_validate(data)

    async def list_tickets(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        sort: str = "date",
    ) -> list[Ticket]:
        params: dict[str, str] = {"sort": sort}
        if status:
            params["status"] = status
        if priority:
            params["priority"] = priority
        data = self._check(await self._request("GET", "/api/v1/me/tickets", params=params))
        return [Ticket.model_validate(t) for t in (data or [])]

    async def get_ticket(self, ticket_id: int) -> Ticket:
        data = self._check(await self._request("GET", f"/api/v1/tickets/{ticket_id}"))
        return Ticket.model_validate(data)

    async def get_customer_system(self, ticket_id: int) -> CustomerSystem:
        data = self._check(
            await self._request("GET", f"/api/v1/tickets/{ticket_id}/customer-system")
        )
        return CustomerSystem.model_validate(data)

    async def get_customer(self, customer_id: int) -> Customer:
        data = self._check(await self._request("GET", f"/api/v1/customers/{customer_id}"))
        return Customer.model_validate(data)

    async def set_status(self, ticket_id: int, status: TicketStatus | str) -> Ticket:
        value = status.value if isinstance(status, TicketStatus) else status
        data = self._check(
            await self._request(
                "PATCH", f"/api/v1/tickets/{ticket_id}/status", json={"status": value}
            )
        )
        return Ticket.model_validate(data)

    async def create_activity(self, activity: ActivityCreate) -> Activity:
        data = self._check(
            await self._request(
                "POST", "/api/v1/activities/create", json=activity.model_dump()
            )
        )
        return Activity.model_validate(data)

    async def reset(self) -> dict[str, Any]:
        return self._check(await self._request("POST", "/api/v1/me/reset")) or {}
