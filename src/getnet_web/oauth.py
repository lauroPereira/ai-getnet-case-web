"""Server-side building blocks for the public client's authorization code flow.

The pending attempt must be held by the initiating browser session, not looked up
by the returned state in a global registry. A caller must consume it once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx

AUTHORIZE_PATH = "/v1/auth/authorize"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")  # the backend's rule for OAUTH_REDIRECT_URIS


def authorization_endpoint(api_url: str, public_url: str = "") -> str:
    """The login page the browser opens: `public_url` (AUTH_AUTHORIZE_URL, the tunnel's single origin) when set, else
    the API's own. Only the browser goes there; the server keeps exchanging, renewing and revoking at `api_url`.
    `public_url` is absolute, https (http only on the loopback), without credentials, query or fragment."""
    if not public_url:
        return api_url.rstrip("/") + AUTHORIZE_PATH
    parts = urlsplit(public_url)
    secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS)
    if not secure or not parts.hostname or "@" in parts.netloc or parts.query or parts.fragment:
        raise ValueError("AUTH_AUTHORIZE_URL must be an absolute https URL (http only on the loopback) without "
                         "credentials, query or fragment")
    return public_url


@dataclass(frozen=True)
class AuthorizationAttempt:
    # Kept out of repr: an attempt in a log, a traceback or a session dump never shows them.
    state: str = field(repr=False)
    verifier: str = field(repr=False)
    issuer: str
    redirect_uri: str
    client_id: str

    @classmethod
    def create(cls, issuer: str, redirect_uri: str, client_id: str) -> AuthorizationAttempt:
        return cls(secrets.token_urlsafe(32), secrets.token_urlsafe(64), issuer, redirect_uri, client_id)

    def authorization_url(self, authorization_endpoint: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode("ascii")).digest()).decode().rstrip("=")
        params = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
                  "code_challenge": challenge, "code_challenge_method": "S256", "state": self.state}
        return f"{authorization_endpoint}?{urlencode(params)}"

    def validate_callback(self, params: dict[str, list[str]]) -> str:
        """The code of a redirect that answers this attempt; anything else raises ValueError before any exchange.
        `iss` is compared exactly with the configured issuer (RFC 9207), `state` in constant time."""
        # Reject duplicate parameters even when their first value happens to match.
        if not isinstance(params, dict) or any(
            not isinstance(params.get(key), list) or len(params[key]) != 1 or not isinstance(params[key][0], str)
            for key in ("state", "iss", "code")
        ) or "error" in params:
            raise ValueError("Invalid authorization response")
        if not hmac.compare_digest(params["state"][0].encode("utf-8"), self.state.encode("utf-8")) or params["iss"][0] != self.issuer:
            raise ValueError("Invalid authorization response")
        code = params["code"][0]
        if not code:
            raise ValueError("Invalid authorization response")
        return code


def _token_response(response: httpx.Response) -> dict:
    """`{access_token, token_type, expires_in, scope, refresh_token}` or an error: an RFC 6749 error raises
    httpx.HTTPStatusError, a 2xx outside the contract raises ValueError."""
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or not all(
        isinstance(body.get(key), str) and body[key] for key in ("access_token", "refresh_token")
    ):
        raise ValueError("Token response outside the contract")
    return body


def exchange_code(http: httpx.Client, attempt: AuthorizationAttempt, code: str) -> dict:
    response = http.post("/v1/auth/token", data={"grant_type": "authorization_code", "client_id": attempt.client_id,
                         "redirect_uri": attempt.redirect_uri, "code": code, "code_verifier": attempt.verifier})
    return _token_response(response)


def refresh(http: httpx.Client, client_id: str, refresh_token: str) -> dict:
    # Do not retry: if the response is lost the old token may already be consumed.
    response = http.post("/v1/auth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                         "refresh_token": refresh_token})
    return _token_response(response)
