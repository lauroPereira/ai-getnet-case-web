"""Backend v1.0.0 task 108 (ADR 0026): the demonstration tunnel publishes one origin, the interface plus only the
issuer's login page. With AUTH_AUTHORIZE_URL set, the browser opens the login at that public address while the server
keeps exchanging, renewing and revoking at BACKEND_API_URL; without it nothing changes. Same harness as
test_oauth_interface (fake issuer on httpx.MockTransport, the browser bridge replaced). Network-free."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import pytest

from getnet_web import oauth
from getnet_web.oauth import authorization_endpoint

from .test_oauth_interface import CLIENT_ID, URL_LABEL, flow, real_app  # noqa: F401 (flow is a fixture)
from .test_v070_interface import BASE_URL

PUBLIC = "https://demo-tunnel.example"  # stands for the ephemeral trycloudflare.com address
PUBLIC_AUTHORIZE = f"{PUBLIC}/v1/auth/authorize"
PUBLIC_REDIRECT = f"{PUBLIC}/"


@pytest.fixture
def tunnel(flow, monkeypatch):  # noqa: F811 (the fixture imported above)
    """The flow with the tunnel's settings; records the address of every client that carried a code or a token."""
    monkeypatch.setenv("AUTH_AUTHORIZE_URL", PUBLIC_AUTHORIZE)
    monkeypatch.setenv("AUTH_REDIRECT_URI", PUBLIC_REDIRECT)
    token_calls: list[str] = []
    exchange, renew = oauth.exchange_code, oauth.refresh

    def recorded_exchange(client, attempt, code):
        token_calls.append(str(client.base_url).rstrip("/"))
        return exchange(client, attempt, code)

    def recorded_refresh(client, client_id, refresh_token):
        token_calls.append(str(client.base_url).rstrip("/"))
        return renew(client, client_id, refresh_token)

    monkeypatch.setattr("getnet_web.oauth.exchange_code", recorded_exchange)
    monkeypatch.setattr("getnet_web.oauth.refresh", recorded_refresh)
    return (*flow, token_calls)


def test_the_browser_opens_the_public_login_and_the_server_stays_on_the_internal_address(tunnel):
    issuer, window, assistant, sessions, token_calls = tunnel
    app = real_app()
    attempt = app.session_state["oauth_attempt"]
    url = urlsplit(window.urls[-1])
    assert f"{url.scheme}://{url.netloc}{url.path}" == PUBLIC_AUTHORIZE
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert query["redirect_uri"] == PUBLIC_REDIRECT and query["client_id"] == CLIENT_ID
    assert query["state"] == attempt.state and attempt.verifier not in window.urls[-1]

    window.redirect()
    app.run()
    assert not app.exception and app.session_state["api_token"]
    assert issuer.requests[0]["redirect_uri"] == [PUBLIC_REDIRECT]  # the exact registered one
    app.session_state["api_token_exp"] = time.time() - 1
    app.run()  # a rotation
    assert issuer.grants() == ["authorization_code", "refresh_token"]
    app.button(key="logout").click().run()
    assert assistant.revoked_refresh == ["refresh-2"] and app.session_state["api_token"] is None

    assert token_calls == [BASE_URL, BASE_URL]  # exchange and refresh at the internal address
    assert sessions and set(sessions) == {BASE_URL}  # the API client and the revocation too
    secrets = ["code-1", attempt.verifier, "refresh-1", "refresh-2", *issuer.access]
    assert not [secret for secret in secrets for public in window.urls if secret in public]  # nothing public


def test_without_the_public_address_the_login_is_the_api_one(flow):  # noqa: F811
    _, window, *_ = flow
    real_app()
    assert window.urls[-1].startswith(f"{BASE_URL}/v1/auth/authorize?")


def test_the_public_login_is_still_offered_only_at_the_paired_backend(tunnel):
    issuer, window, *_ = tunnel
    app = real_app()
    calls = len(window.urls)
    next(item for item in app.text_input if item.label == URL_LABEL).set_value("http://hostile.example:8000").run()
    assert not app.exception and len(window.urls) == calls and app.session_state["oauth_attempt"] is None
    assert any("só está disponível" in str(item.value) for item in app.sidebar.warning)
    assert issuer.requests == []


@pytest.mark.parametrize(
    "value", ["/v1/auth/authorize", "javascript:alert(1)", f"{PUBLIC_AUTHORIZE}?x=1", "https://user@demo.example/a"]
)
def test_an_invalid_public_address_offers_no_login(tunnel, monkeypatch, value):
    issuer, window, *_ = tunnel
    monkeypatch.setenv("AUTH_AUTHORIZE_URL", value)
    app = real_app()
    assert not app.exception and window.urls == [] and app.session_state["oauth_attempt"] is None
    assert any("AUTH_AUTHORIZE_URL" in str(item.value) for item in app.sidebar.error)
    assert issuer.requests == [] and app.session_state["api_token"] is None


def test_authorization_endpoint():
    assert authorization_endpoint("http://localhost:8000/") == "http://localhost:8000/v1/auth/authorize"
    assert authorization_endpoint("http://localhost:8000", "") == "http://localhost:8000/v1/auth/authorize"
    assert authorization_endpoint("http://localhost:8000", PUBLIC_AUTHORIZE) == PUBLIC_AUTHORIZE
    for value in ("v1/auth/authorize", "ftp://demo.example/x", "https://", f"{PUBLIC_AUTHORIZE}#x"):
        with pytest.raises(ValueError):
            authorization_endpoint("http://localhost:8000", value)
