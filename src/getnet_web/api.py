"""HTTP client for the OpenAPI operations generated for the v1 interface."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import httpx

from getnet_web.generated_api import OPERATIONS

log = logging.getLogger("getnet_web.http")


class APIError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class API(Protocol):
    execution_mode: str

    def login(self, access_key: str) -> dict: ...
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


class APIClient:
    execution_mode = "real"

    def __init__(
        self, base_url: str, *, token: str | None = None, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=20.0)
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
            response = self._http.request(method, route, json=body, headers=headers)
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
            data = response.json()
            error = data.get("error", {})
            raise APIError(
                response.status_code, error.get("code", "http_error"), error.get("message", "Backend request failed.")
            )
        return None if response.status_code == 204 else response.json()

    def login(self, access_key: str) -> dict:
        result = self._request("create_session", body={"access_key": access_key}, authenticated=False)
        self._token = result["access_token"]
        return result

    def logout(self) -> None:
        self._request("revoke_session")
        self._token = None

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
