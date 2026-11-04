"""HTTP client for the demo ticketing service (backend task 66b), used by the operator's Ticketing screen.

The service validates the operator JWT issued by the assistant and forwards each act to the assistant. Errors use the
envelope {"error": {"code", "message", "request_id"}}; the assistant's 404/409/422 come back with the same code."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from getnet_web.api import DEFAULT_TIMEOUT_SECONDS, APIError

DEFAULT_TICKETING_URL = "http://127.0.0.1:8002"
STATUSES = ("new", "open", "pending", "solved")
ACTIONS = ("approve", "reply", "reject")


def ticketing_url() -> str:
    return os.environ.get("TICKETING_URL") or DEFAULT_TICKETING_URL


def new_idempotency_key() -> str:
    """8 to 80 characters of [A-Za-z0-9_-] (spec v0.7.0, D10)."""
    return f"web-{uuid.uuid4().hex}"


class TicketingClient:
    def __init__(self, base_url: str, *, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=DEFAULT_TIMEOUT_SECONDS)
        self._token = token

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
        response = self._http.request(
            method, path, params=params, json=body, headers={"Authorization": f"Bearer {self._token}"}
        )
        if response.status_code >= 400:
            try:
                data = response.json()
            except ValueError:
                data = {}
            error = data.get("error", {}) if isinstance(data, dict) else {}
            error = error if isinstance(error, dict) else {"code": str(error)}
            raise APIError(
                response.status_code,
                error.get("code", "http_error"),
                error.get("message", "Ticketing request failed."),
            )
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):  # outside the contract: never let it break the screen
            raise APIError(502, "invalid_response", "The ticketing service answered without a JSON object.")
        return data

    def tickets(self, status: str | None = None) -> list[dict]:
        tickets = self._request("GET", "/tickets", params={"status": status} if status else None).get("tickets")
        if not isinstance(tickets, list):
            raise APIError(502, "invalid_response", "The ticketing service answered without a tickets list.")
        return tickets

    def ticket(self, ticket_id: str) -> dict:
        detail = self._request("GET", f"/tickets/{ticket_id}")
        if not isinstance(detail.get("ticket_id"), str):
            raise APIError(502, "invalid_response", "The ticketing service answered without the ticket.")
        return detail

    def assign(self, ticket_id: str) -> dict:
        return self._request("POST", f"/tickets/{ticket_id}/assign")

    def act(self, ticket_id: str, action: str, text: str, idempotency_key: str) -> dict:
        body = {"action": action, "text": text, "idempotency_key": idempotency_key}
        return self._request("POST", f"/tickets/{ticket_id}/actions", body=body)

    def solve(self, ticket_id: str) -> dict:
        return self._request("POST", f"/tickets/{ticket_id}/solve")


def get_ticketing_client(state: Any, base_url: str, token: str, *, factory=TicketingClient) -> TicketingClient:
    identity = (base_url.rstrip("/"), token)
    current = state.get("_ticketing_client")
    if current is not None and state.get("_ticketing_identity") == identity:
        return current
    close_ticketing_client(state)
    client = factory(base_url, token=token)
    state["_ticketing_client"] = client
    state["_ticketing_identity"] = identity
    return client


def close_ticketing_client(state: Any) -> None:
    client = state.get("_ticketing_client")
    if client is not None:
        client.close()
    state["_ticketing_client"] = None
    state["_ticketing_identity"] = None
