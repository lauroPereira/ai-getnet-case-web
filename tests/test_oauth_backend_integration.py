"""Opt-in: the web client's OAuth functions against the real backend, in process (FastAPI TestClient, scripted model,
no key, no network, no cost). The interface's defaults (issuer, redirect URI, client) are checked against the
backend's: authorize, the issuer's login page, the code exchange with the verifier, the rotation, the revocation of
the whole login and a refresh refused afterwards.

Skipped unless the backend package is importable, so the two repositories stay independent. To run it, use an
environment with the backend's dependencies plus httpx, with both `src` folders on the path:

    PYTHONPATH=<web>/src:<backend>/src python -m pytest tests/test_oauth_backend_integration.py
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from getnet_web.api import APIClient, APIError, token_claims
from getnet_web.oauth import AuthorizationAttempt, exchange_code, refresh

backend_app = pytest.importorskip("merchant_assistant.api.app", reason="backend package not on the path")
backend_identity = pytest.importorskip("merchant_assistant.api.identity")
backend_config = pytest.importorskip("merchant_assistant.config")
backend_testing = pytest.importorskip("merchant_assistant.testing")
testclient = pytest.importorskip("fastapi.testclient")
pydantic = pytest.importorskip("pydantic")

# The interface's defaults (app.py and getnet_web.api), which must match the backend's.
ISSUER = "http://127.0.0.1:8000"
REDIRECT_URI = "http://localhost:8501/"
CLIENT_ID = "merchant-assistant-web"
USER = "cliente1988"
BASE_URL = "http://testserver"  # the TestClient's host


class InProcess(httpx.BaseTransport):
    """Hands the requests of the interface's own httpx clients to the backend's TestClient, which may be built on
    another HTTP library: the web code under test runs on httpx, as in production."""

    def __init__(self, browser):
        self.browser = browser

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self.browser.request(
            request.method, str(request.url), headers=list(request.headers.multi_items()), content=request.read(),
            follow_redirects=False,
        )
        headers = [(k, v) for k, v in answer.headers.multi_items() if k.lower() not in {"content-encoding",
                                                                                         "content-length"}]
        return httpx.Response(answer.status_code, headers=headers, content=answer.content)


@pytest.fixture
def backend(tmp_path):
    """Returns the user's browser at the issuer (the TestClient) and the interface's HTTP client on the same app."""
    key_path = tmp_path / "jwt" / "signing_key.pem"
    backend_identity.load_signing_key(key_path)
    settings = backend_config.Settings(
        _env_file=None,  # never the developer's .env
        openai_api_key=None,
        runtime_dir=tmp_path / "runtime",
        demo_mode=True,
        budget_limit_usd=1.0,
        qdrant_url=":memory:",
        jwt_private_key_path=key_path,
        demo_user_passwords=pydantic.SecretStr(backend_testing.passwords_setting(backend_testing.TEST_USER_PASSWORDS)),
        ticketing_client_secret=pydantic.SecretStr(backend_testing.TEST_TICKETING_SECRET),
        password_hash_iterations=1_000,
    )
    assert settings.jwt_issuer == ISSUER  # the backend's default issuer is the interface's default AUTH_ISSUER
    orchestrator, _ = backend_testing.offline_orchestrator(settings, with_index=False)
    browser = testclient.TestClient(backend_app.create_app(orchestrator, settings))
    web = httpx.Client(base_url=BASE_URL, transport=InProcess(browser))
    try:
        yield browser, web
    finally:
        web.close()
        browser.close()


def web_client(browser, token):
    """The interface's API client (getnet_web.api) on the in-process backend."""
    return APIClient(BASE_URL, token=token, transport=InProcess(browser))


def sign_in(browser, attempt):
    """The user signs in on the issuer's page (the popup); returns the parameters of the redirect to the interface."""
    authorize = urlsplit(attempt.authorization_url(f"{BASE_URL}/v1/auth/authorize"))
    page = browser.get(f"{authorize.path}?{authorize.query}")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    form = {name: values[0] for name, values in parse_qs(authorize.query).items()}
    login = form | {"username": USER, "password": backend_testing.TEST_USER_PASSWORDS[USER]}
    back = browser.post("/v1/auth/authorize", data=login, follow_redirects=False)
    assert back.status_code == 303
    location = urlsplit(back.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
    return parse_qs(location.query)


def test_authorize_login_exchange_refresh_and_revoke(backend):
    browser, web = backend
    attempt = AuthorizationAttempt.create(ISSUER, REDIRECT_URI, CLIENT_ID)
    code = attempt.validate_callback(sign_in(browser, attempt))  # state and the exact iss of the backend

    first = exchange_code(web, attempt, code)
    claims = token_claims(first["access_token"])
    assert claims["iss"] == ISSUER and claims["role"] == "merchant" and claims["client_id"] == CLIENT_ID

    second = refresh(web, CLIENT_ID, first["refresh_token"])
    assert second["refresh_token"] != first["refresh_token"]
    assert token_claims(second["access_token"])["sub"] == claims["sub"]  # the same principal after the rotation

    api = web_client(browser, second["access_token"])
    try:
        assert api.profiles()  # the rotated access token works
        api.logout(second["refresh_token"])
    finally:
        api.close()

    with pytest.raises(httpx.HTTPStatusError) as refused:
        refresh(web, CLIENT_ID, second["refresh_token"])  # the whole login was revoked
    assert refused.value.response.status_code == 400 and refused.value.response.json()["error"] == "invalid_grant"
    probe = web_client(browser, second["access_token"])
    try:
        with pytest.raises(APIError) as revoked:
            probe.profiles()
        assert revoked.value.status_code == 401
    finally:
        probe.close()


def test_a_lost_rotation_is_revoked_with_the_old_refresh_token(backend):
    """What the interface does when a refresh answer is lost: revoke with the access token it still has and the old
    refresh token. The backend revokes the whole login, including the rotation nobody received."""
    browser, web = backend
    attempt = AuthorizationAttempt.create(ISSUER, REDIRECT_URI, CLIENT_ID)
    first = exchange_code(web, attempt, attempt.validate_callback(sign_in(browser, attempt)))
    lost = refresh(web, CLIENT_ID, first["refresh_token"])  # the answer never reached the interface
    api = web_client(browser, first["access_token"])
    try:
        api.logout(first["refresh_token"])
    finally:
        api.close()
    with pytest.raises(httpx.HTTPStatusError) as refused:
        refresh(web, CLIENT_ID, lost["refresh_token"])
    assert refused.value.response.json()["error"] == "invalid_grant"


def test_a_forged_state_or_another_verifier_never_yields_tokens(backend):
    browser, web = backend
    attempt = AuthorizationAttempt.create(ISSUER, REDIRECT_URI, CLIENT_ID)
    params = sign_in(browser, attempt)
    with pytest.raises(ValueError):
        AuthorizationAttempt.create(ISSUER, REDIRECT_URI, CLIENT_ID).validate_callback(params)  # another session
    other = AuthorizationAttempt(attempt.state, "x" * 64, ISSUER, REDIRECT_URI, CLIENT_ID)  # right state, wrong verifier
    with pytest.raises(httpx.HTTPStatusError) as refused:
        exchange_code(web, other, other.validate_callback(params))
    assert refused.value.response.json()["error"] == "invalid_grant"
