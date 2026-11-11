"""Opt-in (backend v1.1.0, task 111): the interface's client reads the availability of profiles and Router engines from
the real backend, in process (scripted model, synthetic keys, no network, no cost). With only the OpenAI key the
DeepSeek profiles and the JEV are unavailable; with every key everything is; no key value reaches the interface.

Skipped unless the backend package is importable (see test_oauth_backend_integration.py for how to run it)."""

from __future__ import annotations

from getnet_web.oauth import AuthorizationAttempt, exchange_code

from .test_oauth_backend_integration import (  # noqa: F401 (backend is a fixture)
    CLIENT_ID,
    ISSUER,
    REDIRECT_URI,
    backend,
    pydantic,
    sign_in,
    web_client,
)

KEYS = {
    "openai_api_key": "synthetic-openai-key-web",
    "deepseek_api_key": "synthetic-deepseek-key-web",
    "typesafe_api_key": "synthetic-typesafe-key-web",
}


def availability(body):
    return {item["profile_id"]: (item["available"], item["missing_providers"]) for item in body["profiles"]}


def test_the_client_reads_what_the_backend_can_run_without_any_key_value(backend):  # noqa: F811
    browser, web = backend
    attempt = AuthorizationAttempt.create(ISSUER, REDIRECT_URI, CLIENT_ID)
    token = exchange_code(web, attempt, attempt.validate_callback(sign_in(browser, attempt)))["access_token"]
    settings = browser.app.state.settings
    api = web_client(browser, token)
    try:
        settings.openai_api_key = pydantic.SecretStr(KEYS["openai_api_key"])
        body = api.profile_listing()
        assert availability(body) == {
            "adjusted": (False, ["deepseek"]),
            "hybrid": (False, ["deepseek"]),
            "reference": (True, []),
        }
        assert body["router_mechanisms"] == {"llm": True, "jev": False}
        assert not any(value in str(body) for value in KEYS.values())

        for field, value in KEYS.items():
            setattr(settings, field, pydantic.SecretStr(value))
        body = api.profile_listing()
        assert all(available for available, _ in availability(body).values())
        assert body["router_mechanisms"] == {"llm": True, "jev": True}
        assert not any(value in str(body) for value in KEYS.values())
        assert api.profiles() == body["profiles"]
    finally:
        api.close()
