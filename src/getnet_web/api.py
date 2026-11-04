"""HTTP client for the OpenAPI operations generated for the v1 interface."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import time
from typing import Any, Protocol

import httpx

from getnet_web.generated_api import OPERATIONS

log = logging.getLogger("getnet_web.http")

# Reads and administrative calls stay short. Operations that wait for paid work in the backend (an attendance,
# the Router comparison, an evaluation, a candidate build or test) get the backend request limit
# (MAX_REQUEST_SECONDS, 90 s by default) plus a margin, so a finished answer is not reported as a timeout.
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("BACKEND_TIMEOUT_SECONDS", "20"))
LONG_TIMEOUT_SECONDS = float(os.environ.get("BACKEND_MESSAGE_TIMEOUT_SECONDS", "105"))
LONG_OPERATIONS = frozenset({"send_v1_message", "compare_routers", "create_evaluation", "kb_build", "kb_test"})

# Public client of the password grant (backend spec v0.7.0, "Contrato entre os times", Identidade).
WEB_CLIENT_ID = os.environ.get("AUTH_CLIENT_ID", "merchant-assistant-web")
SESSION_ENDED_CODES = frozenset({"token_required", "invalid_token"})


class APIError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def token_claims(token: str | None) -> dict:
    """Claims of a JWT read without verification, only to label the session in the interface (role, principal, expiry).
    Authorization is always decided by the backend and the ticketing service."""
    try:
        payload = (token or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, binascii.Error):
        return {}
    return claims if isinstance(claims, dict) else {}


def session_ended(exc: Exception) -> bool:
    """A 401 from the backend or the ticketing service: the token is missing, expired or revoked."""
    return isinstance(exc, APIError) and exc.status_code == 401 and exc.code in SESSION_ENDED_CODES


class API(Protocol):
    execution_mode: str

    def login(self, username: str, password: str) -> dict: ...
    def logout(self) -> None: ...
    def model_catalog(self) -> dict: ...
    def profiles(self) -> list[dict]: ...
    def compare(self, payload: dict) -> dict: ...
    def send_message(self, payload: dict) -> dict: ...
    def run(self, request_id: str) -> dict: ...
    def conversation(self, conversation_id: str) -> dict: ...
    def create_profile(self, payload: dict) -> dict: ...
    def update_profile(self, profile_id: str, payload: dict) -> dict: ...
    def publish_profile(self, profile_id: str, expected_version: int) -> dict: ...
    def kb_documents(self) -> list[dict]: ...
    def kb_document(self, document_id: str) -> dict: ...
    def kb_save_draft(self, document_id: str, payload: dict) -> dict: ...
    def kb_diff(self, document_id: str) -> dict: ...
    def kb_estimate(self, revisions: dict) -> dict: ...
    def kb_build(self, payload: dict) -> dict: ...
    def kb_versions(self) -> dict: ...
    def kb_test(self, version_id: str, payload: dict) -> dict: ...
    def kb_activate(self, version_id: str, payload: dict) -> dict: ...
    def kb_rollback(self, version_id: str, payload: dict) -> dict: ...
    def scenarios(self) -> list[dict]: ...
    def estimate_evaluation(self, request_id: str) -> dict: ...
    def evaluate(self, request_id: str, payload: dict) -> dict: ...
    def estimate_comparison(self, payload: dict) -> dict: ...
    def create_comparison(self, payload: dict) -> dict: ...
    def comparison(self, comparison_id: str) -> dict: ...


class APIClient:
    execution_mode = "real"

    def __init__(
        self, base_url: str, *, token: str | None = None, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=DEFAULT_TIMEOUT_SECONDS)
        self._token = token

    def close(self) -> None:
        self._http.close()

    def _request(
        self, operation: str, *, path_params: dict | None = None, body: dict | None = None, authenticated: bool = True
    ) -> Any:
        method, route = OPERATIONS[operation]
        for key, value in (path_params or {}).items():
            route = route.replace("{" + key + "}", str(value))
        headers = {}
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        started = time.perf_counter()
        try:
            timeout = LONG_TIMEOUT_SECONDS if operation in LONG_OPERATIONS else DEFAULT_TIMEOUT_SECONDS
            response = self._http.request(method, route, json=body, headers=headers, timeout=timeout)
        except httpx.TransportError as exc:
            log.warning(
                "Backend HTTP transport failed",
                extra={
                    "event": {
                        "operation": operation,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "error_class": type(exc).__name__,
                    }
                },
            )
            raise
        if response.status_code >= 400:
            try:
                data = response.json()
            except ValueError:  # a proxy or gateway answered without the structured error body
                data = {}
            error = data.get("error", {}) if isinstance(data, dict) else {}
            raise APIError(
                response.status_code, error.get("code", "http_error"), error.get("message", "Backend request failed.")
            )
        return None if response.status_code == 204 else response.json()

    def login(self, username: str, password: str) -> dict:
        """Password grant (RFC 6749) for the public web client. Errors follow RFC 6749 section 5.2."""
        form = {"grant_type": "password", "username": username, "password": password, "client_id": WEB_CLIENT_ID}
        try:
            method, route = OPERATIONS["auth_token"]  # form encoded (RFC 6749), not JSON
            response = self._http.request(method, route, data=form)
        except httpx.TransportError as exc:
            log.warning("Backend login transport failed", extra={"event": {"error_class": type(exc).__name__}})
            raise
        if response.status_code >= 400:
            try:
                data = response.json()
            except ValueError:
                data = {}
            code = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), str) else "http_error"
            message = (data.get("error_description") if isinstance(data, dict) else None) or "Login failed."
            if response.status_code == 429:
                retry = response.headers.get("Retry-After")
                code, message = "too_many_attempts", f"Too many attempts. Retry after {retry or 'a few'} seconds."
            raise APIError(response.status_code, code, message)
        result = response.json()
        self._token = result["access_token"]
        return result

    def logout(self) -> None:
        """Revokes the token's jti on the backend (POST /v1/auth/revoke)."""
        method, route = OPERATIONS["auth_revoke"]
        response = self._http.request(method, route, headers={"Authorization": f"Bearer {self._token}"})
        self._token = None
        if response.status_code >= 400 and response.status_code != 401:
            raise APIError(response.status_code, "revoke_failed", "The backend did not confirm the logout.")

    def model_catalog(self) -> dict:
        return self._request("model_catalog")

    def profiles(self) -> list[dict]:
        return self._request("list_profiles")["profiles"]

    def compare(self, payload: dict) -> dict:
        return self._request("compare_routers", body=payload)

    def send_message(self, payload: dict) -> dict:
        return self._request("send_v1_message", body=payload)

    def run(self, request_id: str) -> dict:
        return self._request("get_run", path_params={"request_id": request_id})

    def conversation(self, conversation_id: str) -> dict:
        return self._request("get_conversation", path_params={"conversation_id": conversation_id})

    def create_profile(self, payload: dict) -> dict:
        return self._request("create_profile", body=payload)

    def update_profile(self, profile_id: str, payload: dict) -> dict:
        return self._request("update_profile", path_params={"profile_id": profile_id}, body=payload)

    def publish_profile(self, profile_id: str, expected_version: int) -> dict:
        return self._request(
            "publish_profile", path_params={"profile_id": profile_id}, body={"expected_version": expected_version}
        )

    def kb_documents(self) -> list[dict]:
        return self._request("kb_documents")["documents"]

    def kb_document(self, document_id: str) -> dict:
        return self._request("kb_document", path_params={"document_id": document_id})

    def kb_save_draft(self, document_id: str, payload: dict) -> dict:
        return self._request("kb_save_draft", path_params={"document_id": document_id}, body=payload)

    def kb_diff(self, document_id: str) -> dict:
        return self._request("kb_diff", path_params={"document_id": document_id})

    def kb_estimate(self, revisions: dict) -> dict:
        return self._request("kb_estimate", body={"revisions": revisions})

    def kb_build(self, payload: dict) -> dict:
        return self._request("kb_build", body=payload)

    def kb_versions(self) -> dict:
        return self._request("kb_versions")

    def kb_test(self, version_id: str, payload: dict) -> dict:
        return self._request("kb_test", path_params={"version_id": version_id}, body=payload)

    def kb_activate(self, version_id: str, payload: dict) -> dict:
        return self._request("kb_activate", path_params={"version_id": version_id}, body=payload)

    def kb_rollback(self, version_id: str, payload: dict) -> dict:
        return self._request("kb_rollback", path_params={"version_id": version_id}, body=payload)

    def scenarios(self) -> list[dict]:
        return self._request("list_scenarios")["scenarios"]

    def estimate_evaluation(self, request_id: str) -> dict:
        return self._request("estimate_evaluation", path_params={"request_id": request_id})

    def evaluate(self, request_id: str, payload: dict) -> dict:
        return self._request("create_evaluation", path_params={"request_id": request_id}, body=payload)

    def estimate_comparison(self, payload: dict) -> dict:
        return self._request("estimate_comparison", body=payload)

    def create_comparison(self, payload: dict) -> dict:
        return self._request("create_comparison", body=payload)

    def comparison(self, comparison_id: str) -> dict:
        return self._request("get_comparison", path_params={"comparison_id": comparison_id})


def get_session_client(state: Any, base_url: str, token: str, *, factory=APIClient) -> APIClient:
    identity = (base_url.rstrip("/"), token)
    current = state.get("_getnet_api_client")
    if current is not None and state.get("_getnet_api_identity") == identity:
        return current
    close_session_client(state)
    client = factory(base_url, token=token)
    state["_getnet_api_client"] = client
    state["_getnet_api_identity"] = identity
    return client


def close_session_client(state: Any) -> None:
    client = state.get("_getnet_api_client")
    if client is not None:
        client.close()
    state["_getnet_api_client"] = None
    state["_getnet_api_identity"] = None
