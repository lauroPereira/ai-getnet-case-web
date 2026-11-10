"""v0.13.0 login by redirection (backend task 97, CA-68): the Streamlit session against a fake issuer. The real
`getnet_web.oauth` functions run on httpx.MockTransport, so the form sent to the token endpoint is checked on the wire;
only the browser bridge (the popup and its BroadcastChannel) is replaced. Network-free."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from streamlit.runtime.scriptrunner_utils.script_requests import RerunData
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
from streamlit.testing.v1 import AppTest

from getnet_web import oauth
from getnet_web.api import APIClient, APIError

from .test_interface import ROOT
from .test_v070_interface import BASE_URL, ISSUER, FakeAssistant, make_token, texts

REDIRECT_URI = "http://localhost:8501/"
CLIENT_ID = "merchant-assistant-web"
REAL_EXCHANGE, REAL_REFRESH = oauth.exchange_code, oauth.refresh
URL_LABEL = "URL da API backend"


class Issuer:
    """The backend's token endpoint. Answers in order from `answers` (a Response or an exception to raise), then with
    a fresh token pair for `sub`."""

    def __init__(self, sub="merchant:cliente1988", role="merchant"):
        self.sub, self.role = sub, role
        self.requests: list[dict[str, list[str]]] = []
        self.answers: list = []
        self.issued = 0
        self.during: list = []  # callables run, once each, while a request is being answered

    def handle(self, request):
        assert request.url.path == "/v1/auth/token"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        self.requests.append(parse_qs(request.content.decode()))
        while self.during:
            self.during.pop(0)()
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.issued += 1
        return httpx.Response(200, json={
            "access_token": make_token(self.role, self.sub), "token_type": "Bearer", "expires_in": 3600,
            "scope": "support:read", "refresh_token": f"refresh-{self.issued}",
        })

    def grants(self):
        return [request["grant_type"][0] for request in self.requests]


class LoginWindow:
    """Stands for getnet_web.oauth_bridge.login_popup: records the authorization URL and returns, once, the redirect
    that `next` builds from the attempt's state."""

    def __init__(self):
        self.urls: list[str] = []
        self.expires: list[float] = []
        self.next = None
        self.times = 1  # how many renders return the redirect (a repeated delivery returns it again)
        self.delivered = None

    def __call__(self, url, state, expires_in):
        self.urls.append(url)
        self.expires.append(expires_in)
        if self.next is None:
            return None
        if self.delivered is None:
            self.delivered = self.next(state)  # a repeated delivery is the very same redirect
        result = dict(self.delivered)
        self.times -= 1
        if self.times <= 0:
            self.next, self.delivered = None, None
        return result

    def redirect(self, times=1, **params):
        self.times, self.delivered = times, None
        self.next = lambda state: {"code": ["code-1"], "state": [state], "iss": [ISSUER]} | params


def rerun_requested():
    """What the browser does when it sends a new widget value (a second delivery of the redirect, a click) while the
    script runs: Streamlit stops the running script at its next yield point (any session-state access, any `st` call)
    and starts it again."""
    get_script_run_ctx().script_requests.request_rerun(RerunData())


@pytest.fixture
def flow(monkeypatch):
    for name in ("AUTH_ISSUER", "AUTH_REDIRECT_URI", "BACKEND_API_URL"):
        monkeypatch.delenv(name, raising=False)
    issuer, window, assistant = Issuer(), LoginWindow(), FakeAssistant("merchant", "merchant:cliente1988")
    transport = httpx.MockTransport(issuer.handle)

    def exchange(_client, attempt, code):
        with httpx.Client(base_url=BASE_URL, transport=transport) as client:
            return REAL_EXCHANGE(client, attempt, code)

    def renew(_client, client_id, refresh_token):
        with httpx.Client(base_url=BASE_URL, transport=transport) as client:
            return REAL_REFRESH(client, client_id, refresh_token)

    sessions = []

    def session_client(_state, base_url, _token):
        sessions.append(base_url)
        return assistant

    monkeypatch.setattr("getnet_web.oauth.exchange_code", exchange)
    monkeypatch.setattr("getnet_web.oauth.refresh", renew)
    monkeypatch.setattr("getnet_web.oauth_bridge.login_popup", window)
    monkeypatch.setattr("getnet_web.api.get_session_client", session_client)
    return issuer, window, assistant, sessions


def real_app():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    app.selectbox(key="mode").select("Real (API)").run()
    assert not app.exception
    return app


def signed_in(flow):
    issuer, window, *_ = flow
    app = real_app()
    window.redirect()
    app.run()
    assert not app.exception and app.session_state["api_token"] and issuer.grants() == ["authorization_code"]
    return app


def all_texts(app):
    return texts(app) + " ".join(str(item.value) for item in [*app.title, *app.subheader, *app.header])


# --- the authorization request and the redirect ----------------------------------------------------------------------


def test_a_valid_redirect_installs_the_tokens_and_none_reaches_the_page(flow):
    issuer, window, *_ = flow
    app = real_app()
    attempt = app.session_state["oauth_attempt"]
    url = urlsplit(window.urls[-1])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BASE_URL}/v1/auth/authorize"
    query = parse_qs(url.query)
    assert {key: values[0] for key, values in query.items() if key != "code_challenge"} == {
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256", "state": attempt.state,
    }
    assert attempt.verifier not in window.urls[-1]  # only the challenge leaves the server
    assert attempt.verifier not in repr(app.session_state) and attempt.state not in repr(app.session_state)

    window.redirect()
    app.run()
    assert not app.exception
    assert issuer.requests == [{
        "grant_type": ["authorization_code"], "client_id": [CLIENT_ID], "redirect_uri": [REDIRECT_URI],
        "code": ["code-1"], "code_verifier": [attempt.verifier],
    }]
    assert app.session_state["api_refresh_token"] == "refresh-1"
    assert app.session_state["api_principal"] == "merchant:cliente1988"
    assert [tab.label for tab in app.tabs] == ["Atendimento", "Resultados"]
    shown = all_texts(app)
    for secret in (app.session_state["api_token"], "refresh-1", "code-1", attempt.verifier):
        assert secret not in shown


@pytest.mark.parametrize("deliveries", [2, 1], ids=["delivered-twice", "another-rerun"])
def test_a_rerun_during_the_code_exchange_still_opens_the_session(flow, deliveries):
    """D1 of the v0.13.0 QA: the redirect reached the tab twice; the second delivery asked for a rerun while the first
    run was exchanging the code, Streamlit stopped that run before the tokens were kept, and the next run, without
    them, started a new attempt: a login opened at the backend and held by nobody. Now the answer is kept before any
    yield point and the next run installs it; the same redirect delivered again never exchanges a second time."""
    issuer, window, *_ = flow
    app = real_app()
    issuer.during.append(rerun_requested)
    window.redirect(times=deliveries)
    app.run()
    assert not app.exception
    assert issuer.grants() == ["authorization_code"]  # one exchange
    assert app.session_state["api_token"] and app.session_state["api_refresh_token"] == "refresh-1"
    assert [tab.label for tab in app.tabs] == ["Atendimento", "Resultados"]
    assert not app.sidebar.warning
    app.run()
    assert issuer.grants() == ["authorization_code"] and app.session_state["api_refresh_token"] == "refresh-1"


def test_a_rerun_during_the_refresh_keeps_the_rotated_tokens(flow):
    issuer, *_ = flow
    app = signed_in(flow)
    issuer.during.append(rerun_requested)
    app.session_state["api_token_exp"] = time.time() - 1
    app.run()
    assert not app.exception
    assert issuer.grants() == ["authorization_code", "refresh_token"]
    assert app.session_state["api_refresh_token"] == "refresh-2" and app.session_state["api_token"]
    assert app.session_state["api_token_exp"] > time.time() + 3000 and app.tabs


def lock_is_held(seen):
    """Run while the token endpoint answers: records whether the session's token lock is held (a run started
    meanwhile by a fast rerun waits on it)."""
    def check():
        lock = get_script_run_ctx().session_state["token_lock"]
        free = lock.acquire(blocking=False)
        if free:
            lock.release()
        seen.append(not free)
    return check


def test_the_token_calls_hold_the_session_lock(flow):
    """Streamlit starts a rerun at once and stops the running script only at its next yield point, so two runs of a
    session can overlap. The run that waits for the token endpoint holds the lock; another one waits for its answer."""
    issuer, *_ = flow
    seen = []
    issuer.during.append(lock_is_held(seen))
    app = signed_in(flow)
    issuer.during.append(lock_is_held(seen))
    app.session_state["api_token_exp"] = time.time() - 1
    app.run()
    assert seen == [True, True] and issuer.grants() == ["authorization_code", "refresh_token"]
    lock = app.session_state["token_lock"]
    assert lock.acquire(blocking=False)  # released once the run is over
    lock.release()


def test_a_new_notice_replaces_the_previous_one(flow):
    """O3 of the v0.13.0 QA: a login notice is shown once, not kept beside newer ones."""
    _, window, *_ = flow
    app = real_app()
    window.redirect(state=["forged-state"])
    app.run()
    assert [str(item.value) for item in app.sidebar.warning] == [
        "Não foi possível entrar. Clique em Entrar e tente novamente."
    ]
    next(item for item in app.text_input if item.label == URL_LABEL).set_value("http://hostile.example:8000").run()
    assert len(app.sidebar.warning) == 1 and "só está disponível" in str(app.sidebar.warning[0].value)
    app.run()
    assert len(app.sidebar.warning) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"state": ["forged-state"]},
        {"iss": ["https://another-issuer.example"]},
        {"iss": [ISSUER + "/"]},  # compared exactly, without normalisation
        {"code": ["code-1", "code-2"]},
        {"code": []},
        {"error": ["access_denied"], "error_description": ["The user refused."]},
    ],
    ids=["wrong-state", "wrong-iss", "iss-not-exact", "duplicate-code", "no-code", "error"],
)
def test_an_invalid_redirect_never_exchanges_a_code(flow, params):
    issuer, window, *_ = flow
    app = real_app()
    first_state = app.session_state["oauth_attempt"].state
    window.redirect(**params)
    app.run()
    assert not app.exception and issuer.requests == []
    assert app.session_state["api_token"] is None and not app.tabs
    assert any("Não foi possível entrar" in str(item.value) for item in app.sidebar.warning)
    assert app.session_state["oauth_attempt"].state != first_state  # a spent attempt is never offered again


def test_a_duplicated_state_is_refused_even_when_one_value_matches(flow):
    issuer, window, *_ = flow
    app = real_app()
    window.next = lambda state: {"code": ["code-1"], "state": [state, state], "iss": [ISSUER]}
    app.run()
    assert issuer.requests == [] and app.session_state["api_token"] is None


def test_a_refused_or_unreachable_exchange_keeps_the_login_screen(flow):
    issuer, window, *_ = flow
    app = real_app()
    issuer.answers.append(httpx.Response(400, json={"error": "invalid_grant", "error_description": "x"}))
    window.redirect()
    app.run()
    assert app.session_state["api_token"] is None
    assert any("Não foi possível entrar" in str(item.value) for item in app.sidebar.warning)
    issuer.answers.append(httpx.ConnectError("backend down"))
    window.redirect()
    app.run()
    assert app.session_state["api_token"] is None and len(issuer.requests) == 2
    assert any("Backend inacessível" in str(item.value) for item in app.sidebar.warning)


def test_the_button_knows_the_deadline_and_a_late_click_asks_for_a_new_attempt(flow):
    issuer, window, *_ = flow
    app = real_app()
    assert 299 <= window.expires[-1] <= 300
    first = app.session_state["oauth_attempt"]
    app.session_state["oauth_started"] = time.monotonic() - 200
    app.run()
    assert 99 <= window.expires[-1] <= 100 and app.session_state["oauth_attempt"] is first
    window.next = lambda state: {"expired": True}  # what the component sends when clicked after the deadline
    app.run()
    assert issuer.requests == [] and app.session_state["api_token"] is None
    assert app.session_state["oauth_attempt"].state != first.state
    assert any("expirou" in str(item.value) for item in app.sidebar.warning)


def test_an_unfinished_attempt_is_renewed_after_five_minutes(flow):
    issuer, window, *_ = flow
    app = real_app()
    first = app.session_state["oauth_attempt"]
    app.session_state["oauth_started"] = time.monotonic() - 301
    app.run()
    renewed = app.session_state["oauth_attempt"]
    assert renewed.state != first.state and renewed.verifier != first.verifier
    assert f"state={renewed.state}" in window.urls[-1]
    # a redirect that arrives after the attempt expired is not exchanged either
    app.session_state["oauth_started"] = time.monotonic() - 301
    window.redirect()
    app.run()
    assert issuer.requests == [] and app.session_state["api_token"] is None
    assert any("expirou" in str(item.value) for item in app.sidebar.warning)


# --- the rotating refresh token ----------------------------------------------------------------------------------------


def test_the_refresh_rotates_and_sends_the_old_token_once(flow):
    issuer, *_ = flow
    app = signed_in(flow)
    app.session_state["api_token_exp"] = time.time() + 10  # inside the 30 s margin
    app.run()
    assert not app.exception
    assert issuer.requests[-1] == {
        "grant_type": ["refresh_token"], "client_id": [CLIENT_ID], "refresh_token": ["refresh-1"]
    }
    assert app.session_state["api_refresh_token"] == "refresh-2"
    assert app.session_state["api_token_exp"] > time.time() + 3000  # the new access token's expiry
    assert [tab.label for tab in app.tabs] == ["Atendimento", "Resultados"]
    for _ in range(2):
        app.run()
    assert issuer.grants() == ["authorization_code", "refresh_token"]  # fresh again: no further rotation
    assert sum(request.get("refresh_token") == ["refresh-1"] for request in issuer.requests) == 1


@pytest.mark.parametrize(
    ("answer", "revoked"),
    [
        # a lost answer: the rotation may have happened, so the login is revoked once (not at the token endpoint)
        (httpx.ReadTimeout("lost answer"), ["refresh-1"]),
        (httpx.ConnectError("backend down"), ["refresh-1"]),
        (httpx.Response(400, json={"error": "invalid_grant", "error_description": "x"}), []),
        (httpx.Response(200, json={"access_token": "only-an-access-token"}), []),  # outside the contract
        (httpx.Response(200, text="<html>proxy</html>"), []),
    ],
    ids=["timeout", "unreachable", "invalid-grant", "no-refresh-token", "not-json"],
)
def test_a_failed_refresh_ends_the_session_without_reusing_the_token(flow, answer, revoked):
    issuer, _, assistant, sessions = flow
    app = signed_in(flow)
    issuer.answers.append(answer)
    app.session_state["api_token_exp"] = time.time() - 1
    app.run()
    assert not app.exception
    assert app.session_state["api_token"] is None and app.session_state["api_refresh_token"] is None
    assert any("expirou" in str(item.value) for item in app.sidebar.warning) and not app.tabs
    for _ in range(2):
        app.run()
    assert issuer.grants() == ["authorization_code", "refresh_token"]
    assert assistant.revoked_refresh == revoked and all(url == BASE_URL for url in sessions)


def test_an_interrupted_refresh_never_sends_the_old_token_again(flow):
    """The old refresh token leaves the session before the call: even a failure outside the handled ones (the run
    stops between the call and the installation) cannot leave it there to be sent a second time."""
    issuer, *_ = flow
    app = signed_in(flow)
    issuer.answers.append(RuntimeError("run interrupted"))
    app.session_state["api_token_exp"] = time.time() - 1
    app.run()
    assert app.exception  # the unexpected failure surfaces, the session state is what it was at that point
    assert app.session_state["api_refresh_token"] is None
    for _ in range(2):
        app.run()
    assert sum(request.get("refresh_token") == ["refresh-1"] for request in issuer.requests) == 1
    assert issuer.grants() == ["authorization_code", "refresh_token"]
    assert app.session_state["api_token"] is None and app.session_state["api_refresh_token"] is None


def test_a_refresh_for_another_principal_ends_the_session(flow):
    issuer, *_ = flow
    app = signed_in(flow)
    issuer.sub = "merchant:cliente2000"
    app.session_state["api_token_exp"] = time.time() - 1
    app.run()
    assert not app.exception
    assert app.session_state["api_token"] is None and app.session_state["api_principal"] is None
    assert app.session_state["api_refresh_token"] is None and not app.tabs


# --- leaving: another backend address and the logout ----------------------------------------------------------------


def test_another_backend_address_ends_the_session_and_revokes_it_where_it_was_issued(flow):
    _, window, assistant, sessions = flow
    app = signed_in(flow)
    other = "http://127.0.0.1:9000"
    next(item for item in app.text_input if item.label == URL_LABEL).set_value(other).run()
    assert not app.exception
    assert sessions[-1] == BASE_URL and assistant.revoked_refresh == ["refresh-1"]  # never sent to the new address
    assert app.session_state["api_token"] is None and not app.tabs
    assert app.session_state["conversation_owner"] is None
    assert any("URL da API mudou" in str(item.value) for item in app.sidebar.warning)
    assert all(url.startswith(f"{BASE_URL}/v1/auth/authorize?") for url in window.urls)  # no login at the new one


def test_the_login_is_offered_only_at_the_backend_paired_with_the_issuer(flow):
    """Mix-up (RFC 9207): another backend typed in the sidebar could relay the authorize request to the real issuer
    and receive the code and the verifier at its token endpoint. No attempt is made for it."""
    issuer, window, *_ = flow
    app = real_app()
    calls = len(window.urls)
    window.redirect()  # even a redirect waiting in the browser has no attempt to answer
    next(item for item in app.text_input if item.label == URL_LABEL).set_value("http://hostile.example:8000").run()
    assert not app.exception
    assert len(window.urls) == calls and app.session_state["oauth_attempt"] is None
    assert issuer.requests == [] and app.session_state["api_token"] is None
    assert any("só está disponível" in str(item.value) and BASE_URL in str(item.value) for item in app.sidebar.warning)
    window.next = None
    next(item for item in app.text_input if item.label == URL_LABEL).set_value(BASE_URL).run()
    assert window.urls[-1].startswith(f"{BASE_URL}/v1/auth/authorize?") and app.session_state["oauth_attempt"]


def test_the_compose_issuer_is_paired_with_the_default_address(flow, monkeypatch):
    issuer, window, *_ = flow
    monkeypatch.setenv("AUTH_ISSUER", "http://api:8000")  # the backend's JWT_ISSUER under docker compose
    app = real_app()
    window.redirect()  # iss of the local default: refused
    app.run()
    assert issuer.requests == [] and app.session_state["api_token"] is None
    window.redirect(iss=["http://api:8000"])
    app.run()
    assert not app.exception and issuer.grants() == ["authorization_code"] and app.session_state["api_token"]


def test_logout_revokes_the_access_token_and_the_refresh_family():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    client = APIClient("https://backend.example", token="access", transport=httpx.MockTransport(respond))
    try:
        client.logout("refresh")
    finally:
        client.close()
    assert requests[0].url.path == "/v1/auth/revoke" and requests[0].headers["authorization"] == "Bearer access"
    assert parse_qs(requests[0].content.decode()) == {"token": ["refresh"], "token_type_hint": ["refresh_token"]}


@pytest.mark.parametrize(("refresh_token", "confirmed"), [(None, True), ("refresh", False)])
def test_logout_answered_401(refresh_token, confirmed):
    """A 401 means the access token is already invalid: enough without a refresh token, but then the backend did not
    revoke the refresh family, so the logout is not confirmed."""
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"code": "invalid_token"}}))
    client = APIClient("https://backend.example", token="access", transport=transport)
    try:
        if confirmed:
            client.logout(refresh_token)
        else:
            with pytest.raises(APIError):
                client.logout(refresh_token)
    finally:
        client.close()


def test_logout_on_the_screen_sends_the_refresh_token(flow):
    _, _, assistant, _ = flow
    app = signed_in(flow)
    app.button(key="logout").click().run()
    assert assistant.revoked_refresh == ["refresh-1"]
    assert app.session_state["api_token"] is None and app.session_state["api_refresh_token"] is None


# --- the redirect page (the popup) ---------------------------------------------------------------------------------------


def test_the_redirect_page_hands_the_parameters_over_without_rendering_the_app(monkeypatch):
    forwarded = []
    monkeypatch.setattr("getnet_web.oauth_bridge.return_from_popup", forwarded.append)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    app.query_params.update({"code": "secret-code-value", "state": "some-state", "iss": ISSUER})
    app.run()
    assert not app.exception
    assert forwarded == [{"code": ["secret-code-value"], "state": ["some-state"], "iss": [ISSUER]}]
    assert not app.title and not app.tabs and not app.sidebar.selectbox and not app.text_input
    assert "secret-code-value" not in all_texts(app)
    assert "oauth_attempt" not in app.session_state  # the popup's session never holds an attempt


def test_the_redirect_page_runs_with_the_real_bridge():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    app.query_params.update({"error": "access_denied", "state": "some-state", "iss": ISSUER})
    app.run()
    assert not app.exception and not app.title and not app.tabs
