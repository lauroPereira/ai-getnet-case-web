"""v0.7.0 interface: login by user and password (JWT of the assistant), areas by role and the operator's Ticketing
screen. Network-free: fake assistant and ticketing clients, and httpx.MockTransport for the real HTTP clients."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from getnet_web.api import APIClient, APIError, session_ended, token_claims
from getnet_web.simulated import SimulatedAPI
from getnet_web.ticketing import TicketingClient

from .test_interface import ROOT

PASSWORD = "op-ana-password-fictitious"


def make_token(role="operator", sub="operator:op_ana", exp_in=3600) -> str:
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"sub": sub, "role": role, "exp": int(time.time()) + exp_in, "aud": ["merchant-assistant"]}
    return f"{part({'alg': 'RS256', 'typ': 'at+jwt', 'kid': 'k1'})}.{part(claims)}.c2lnbmF0dXJl"


class FakeAssistant:
    """Stands for the assistant API: password login, revocation, profiles and messages."""

    execution_mode = "real"

    def __init__(self, role="operator", sub="operator:op_ana", reply=None, profiles_error=None):
        self.role, self.sub, self.reply, self.profiles_error = role, sub, reply, profiles_error
        self.logins: list[tuple[str, str]] = []
        self.revoked = 0
        self.posts: list[str] = []

    def __call__(self, *_args, **_kwargs):  # used as the APIClient factory and as get_session_client
        return self

    def __getattr__(self, name):  # reads of the other areas (admin catalog, KB, scenarios) come from the fixtures
        return getattr(SimulatedAPI(), name)

    def kb_documents(self):
        return []

    def kb_versions(self):
        return {"versions": []}

    def close(self):
        return None

    def login(self, username, password):
        self.logins.append((username, password))
        return {"access_token": make_token(self.role, self.sub), "token_type": "Bearer", "expires_in": 3600}

    def logout(self):
        self.revoked += 1

    def profiles(self):
        if self.profiles_error:
            raise self.profiles_error
        return SimulatedAPI().profiles()

    def scenarios(self):
        return []

    def send_message(self, payload):
        self.posts.append("send_message")
        return {"message": self.reply | {"conversation_id": payload["conversation_id"]}, "run": None}


def ticket(ticket_id, status="new", **fields):
    return {
        "ticket_id": ticket_id,
        "case_id": f"case_{ticket_id}",
        "protocol": f"HO-20261103-{ticket_id[-6:].upper()}",
        "customer_id": "cliente1988",
        "language": "pt",
        "reason": "customer_request",
        "summary": f"Resumo do {ticket_id}",
        "pending_question": "Quando cai o dinheiro?",
        "draft_reply": "Rascunho: o crédito cai amanhã.",
        "transcript": [{"role": "user", "text": "Quero falar com uma pessoa", "at": "2026-11-03T10:00:00Z"}],
        "status": status,
        "assignee": None,
        "delivery": None,
        "events": [{"at": "2026-11-03T10:00:00Z", "kind": "created", "from_status": None, "to_status": "new"}],
        "deliveries": {"received": [{"event_id": "evt_1", "event_type": "handoff.created"}], "sent": []},
    } | fields


class FakeTicketing:
    """Ticketing service double: same answers as the 66b API, with failures injected per operation."""

    def __init__(self, *items):
        self.items = {item["ticket_id"]: item for item in items}
        self.posts: list[tuple] = []
        self.gets = 0
        self.fail: dict[str, list] = {}
        self.keys: dict[str, dict] = {}

    def __call__(self, *_args, **_kwargs):  # used as get_ticketing_client
        return self

    def _maybe_fail(self, operation):
        queue = self.fail.get(operation) or []
        if queue:
            raise queue.pop(0)

    def _delivered(self, ticket_id, status="delivered"):
        return {"ticket": self.items[ticket_id], "delivery": {"status": status, "event_id": "evt_x"}}

    def tickets(self, status=None):
        self.gets += 1
        return [item for item in self.items.values() if status is None or item["status"] == status]

    def ticket(self, ticket_id):
        self.gets += 1
        return self.items[ticket_id]

    def assign(self, ticket_id):
        self.posts.append(("assign", ticket_id))
        self._maybe_fail("assign")
        self.items[ticket_id] |= {"status": "open", "assignee": "op_ana"}
        return self._delivered(ticket_id)

    def act(self, ticket_id, action, text, idempotency_key):
        self.posts.append(("act", ticket_id, action, text, idempotency_key))
        self._maybe_fail("act")
        if idempotency_key in self.keys:
            return self.keys[idempotency_key]
        item = self.items[ticket_id]
        if item.get("acted"):
            raise APIError(409, "already_resumed", "Case already resumed.")
        pending = self.fail.get("act_pending")
        item |= {"acted": True} | ({} if pending else {"status": "pending", "reply_delivered_at": "2026-11-03T10:05Z"})
        result = self._delivered(ticket_id, "pending" if pending else "delivered")
        self.keys[idempotency_key] = result
        return result

    def solve(self, ticket_id):
        self.posts.append(("solve", ticket_id))
        self._maybe_fail("solve")
        self.items[ticket_id] |= {"status": "solved"}
        return self._delivered(ticket_id)


@pytest.fixture
def wire(monkeypatch):
    def install(assistant, ticketing=None):
        monkeypatch.setattr("getnet_web.api.APIClient", assistant)
        monkeypatch.setattr("getnet_web.api.get_session_client", assistant)
        if ticketing is not None:
            monkeypatch.setattr("getnet_web.ticketing.get_ticketing_client", ticketing)
        return assistant, ticketing

    return install


def real_app(**state):
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    app.selectbox(key="mode").select("Real (API)").run()
    return app


def login(app, username="op_ana", password=PASSWORD):
    app.text_input(key="login_username").set_value(username)
    app.text_input(key="login_password").set_value(password)
    app.button(key="login").click().run()
    assert not app.exception
    return app


def operator_app(wire, ticketing):
    wire(FakeAssistant(), ticketing)
    return login(real_app())


def texts(app):
    return " ".join(
        str(item.value) for item in [*app.markdown, *app.caption, *app.info, *app.warning, *app.error, *app.success]
    )


# --- login and areas by role ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "sub", "tabs"),
    [
        ("merchant", "merchant:cliente1988", ["Atendimento", "Resultados"]),
        ("demo", "demo:default", ["Atendimento", "Resultados"]),
        ("operator", "operator:op_ana", ["Ticketing"]),
        ("admin", "admin:default", ["Atendimento", "Resultados", "Administração"]),
    ],
)
def test_login_by_user_and_password_opens_the_areas_of_the_role(wire, role, sub, tabs):
    assistant, _ = wire(FakeAssistant(role, sub), FakeTicketing())
    app = real_app()
    assert not app.tabs and any("usuário e senha" in str(item.value) for item in app.warning)
    assert not [item for item in app.sidebar.text_input if "chave" in item.label.lower()]  # key login removed
    login(app, sub.split(":")[1], PASSWORD)
    assert assistant.logins == [(sub.split(":")[1], PASSWORD)]
    assert [tab.label for tab in app.tabs] == tabs
    assert app.session_state["api_role"] == role and app.session_state["api_principal"] == sub
    state = repr(app.session_state)
    assert PASSWORD not in state and PASSWORD not in texts(app)  # never kept nor shown


def test_wrong_password_shows_a_neutral_message(wire, monkeypatch):
    class Refusing(FakeAssistant):
        def login(self, username, password):
            raise APIError(400, "invalid_grant", "Invalid credentials.")

    wire(Refusing("merchant"), FakeTicketing())
    app = login(real_app(), "cliente1988", "wrong-password")
    errors = " ".join(str(item.value) for item in app.sidebar.error)
    assert "Usuário ou senha incorretos" in errors and "wrong-password" not in errors
    assert "wrong-password" not in repr(app.session_state)
    assert not app.tabs


def test_logout_revokes_and_returns_to_login(wire):
    assistant, _ = wire(FakeAssistant("merchant", "merchant:cliente1988"), FakeTicketing())
    app = login(real_app(), "cliente1988")
    app.button(key="logout").click().run()
    assert assistant.revoked == 1 and app.session_state["api_token"] is None
    assert not app.tabs


def test_expired_token_goes_back_to_login_with_a_clear_message(wire):
    wire(FakeAssistant("merchant", "merchant:cliente1988"), FakeTicketing())
    app = real_app(
        api_token=make_token("merchant", exp_in=-10),
        api_role="merchant",
        api_principal="merchant:cliente1988",
        api_token_exp=time.time() - 10,
    )
    assert app.session_state["api_token"] is None and not app.tabs
    assert any("expirou" in str(item.value) for item in app.sidebar.warning)


def test_backend_401_invalid_token_also_ends_the_session(wire):
    wire(FakeAssistant("merchant", profiles_error=APIError(401, "invalid_token", "Token expired.")), FakeTicketing())
    app = real_app(api_token="synthetic-session", api_role="merchant")
    assert app.session_state["api_token"] is None
    assert any("expirou" in str(item.value) for item in app.sidebar.warning)


def test_operator_does_not_see_attendance_and_merchant_does_not_see_ticketing(wire):
    ticketing = FakeTicketing(ticket("tkt_000001"))
    app = operator_app(wire, ticketing)
    assert [tab.label for tab in app.tabs] == ["Ticketing"]
    assert not any(button.key == "send_message" for button in app.button)
    wire(FakeAssistant("merchant", "merchant:cliente1988"), ticketing)
    app = login(real_app(), "cliente1988")
    assert "Ticketing" not in [tab.label for tab in app.tabs]
    assert not any(button.key == "ticket_assign" for button in app.button)


# --- CA-47: the ticket cycle on the screen ----------------------------------------------------------------------------


def test_reception_assign_three_actions_pending_and_solve(wire):
    ticketing = FakeTicketing(ticket("tkt_00000a"), ticket("tkt_00000b", "open"), ticket("tkt_00000c", "open"))
    app = operator_app(wire, ticketing)
    assert app.radio(key="ticket_status").value == "new"
    assert any("HO-20261103-00000A" in str(item.value) for item in app.subheader)
    assert "Rascunho: o crédito cai amanhã." in texts(app)

    app.button(key="ticket_assign").click().run()
    assert ticketing.posts == [("assign", "tkt_00000a")]
    assert any("assumido" in str(item.value) for item in app.success)

    app.radio(key="ticket_status").set_value("open").run()
    choice = app.selectbox(key="ticket_choice")
    choice.select(next(o for o in choice.options if "00000A" in o)).run()
    app.radio(key="ticket_action").set_value("approve").run()
    assert app.button(key="ticket_send").disabled  # confirmation first
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert ticketing.posts[-1][:4] == ("act", "tkt_00000a", "approve", "")
    assert any("aceita" in str(item.value) for item in app.success)

    choice = app.selectbox(key="ticket_choice")
    choice.select(next(o for o in choice.options if "00000B" in o)).run()
    app.radio(key="ticket_action").set_value("reply").run()
    app.text_area(key="ticket_text_reply").set_value("O crédito cai amanhã às 9h.").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert ticketing.posts[-1][:4] == ("act", "tkt_00000b", "reply", "O crédito cai amanhã às 9h.")

    ticketing.fail["act_pending"] = [True]  # the ticketing could not reach the assistant: "envio pendente"
    choice = app.selectbox(key="ticket_choice")
    choice.select(next(o for o in choice.options if "00000C" in o)).run()
    app.radio(key="ticket_action").set_value("reject").run()
    app.text_area(key="ticket_text_reject").set_value("Confirme o prazo pela agenda.").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert ticketing.posts[-1][:4] == ("act", "tkt_00000c", "reject", "Confirme o prazo pela agenda.")
    assert any("Envio pendente" in str(item.value) for item in app.warning)

    app.radio(key="ticket_status").set_value("pending").run()
    choice = app.selectbox(key="ticket_choice")
    choice.select(next(o for o in choice.options if "00000A" in o)).run()
    assert any("Resposta entregue" in str(item.value) for item in app.success)
    assert app.button(key="ticket_solve").disabled
    app.checkbox(key="ticket_solve_confirm").check().run()
    app.button(key="ticket_solve").click().run()
    assert ticketing.posts[-1] == ("solve", "tkt_00000a")
    assert ticketing.items["tkt_00000a"]["status"] == "solved"
    assert not app.exception


def test_conflicts_become_messages_with_a_recovery_action(wire):
    ticketing = FakeTicketing(ticket("tkt_00000d"), ticket("tkt_00000e", "open", acted=True))
    ticketing.fail["assign"] = [APIError(409, "claimed_by_other", "Claimed by another operator.")]
    app = operator_app(wire, ticketing)
    app.button(key="ticket_assign").click().run()
    assert any("Outro operador já assumiu" in str(item.value) for item in app.error)
    assert ticketing.items["tkt_00000d"]["status"] == "new"

    app.radio(key="ticket_status").set_value("open").run()
    app.radio(key="ticket_action").set_value("approve").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert any("já recebeu uma ação" in str(item.value) and "Atualize" in str(item.value) for item in app.error)
    gets = ticketing.gets
    app.button(key="ticket_refresh").click().run()
    assert ticketing.gets > gets and not app.exception


def test_retry_after_transport_failure_reuses_the_same_idempotency_key(wire):
    ticketing = FakeTicketing(ticket("tkt_00000f", "open"), ticket("tkt_00000g", "open"))
    ticketing.fail["act"] = [httpx.ConnectError("ticketing down")]
    app = operator_app(wire, ticketing)
    app.radio(key="ticket_status").set_value("open").run()
    choice = app.selectbox(key="ticket_choice")
    choice.select(next(o for o in choice.options if "00000F" in o)).run()
    app.radio(key="ticket_action").set_value("reply").run()
    app.text_area(key="ticket_text_reply").set_value("Resposta sintética.").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert any("mesma chave" in str(item.value) for item in app.warning)
    assert app.checkbox(key="ticket_confirm").value  # still confirmed for the retry
    app.button(key="ticket_send").click().run()
    first, second = ticketing.posts[-2], ticketing.posts[-1]
    assert first[:4] == second[:4] and first[4] == second[4]
    assert 8 <= len(first[4]) <= 80 and all(c.isalnum() or c in "-_" for c in first[4])

    choice = app.selectbox(key="ticket_choice")
    choice.select(next(o for o in choice.options if "00000G" in o)).run()
    app.radio(key="ticket_action").set_value("reply").run()
    app.text_area(key="ticket_text_reply").set_value("Resposta sintética.").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert ticketing.posts[-1][4] != first[4]  # a new click on another ticket gets a new key


def test_navigation_tab_changes_and_reruns_never_post(wire):
    ticketing = FakeTicketing(ticket("tkt_00000h"), ticket("tkt_00000i", "open"), ticket("tkt_00000j", "pending"))
    app = operator_app(wire, ticketing)
    for status in ("open", "pending", "solved", "new", "open"):
        app.radio(key="ticket_status").set_value(status).run()
        app.run()
    app.radio(key="ticket_action").set_value("reject").run()
    app.text_area(key="ticket_text_reject").set_value("instrução").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_refresh").click().run()
    for _ in range(3):
        app.run()
    assert ticketing.posts == [] and not app.exception


def test_ticketing_401_ends_the_operator_session(wire):
    ticketing = FakeTicketing(ticket("tkt_00000k"))
    app = operator_app(wire, ticketing)

    def expired(status=None):
        raise APIError(401, "invalid_token", "Token expired.")

    ticketing.tickets = expired
    app.button(key="ticket_refresh").click().run()
    assert app.session_state["api_token"] is None and not app.tabs
    assert any("expirou" in str(item.value) for item in app.sidebar.warning)


# --- attendance shows the attendant's turn ------------------------------------------------------------------------


def test_attendance_marks_the_attendant_turn(wire):
    reply = {"answer": "Olá, aqui é a Ana: o crédito cai amanhã.", "route": "human_reply", "via": "operator",
             "status": "answered", "request_id": None}
    assistant, _ = wire(FakeAssistant("merchant", "merchant:cliente1988", reply=reply), FakeTicketing())
    app = login(real_app(), "cliente1988")
    app.text_area(key="message_input").set_value("Alguma novidade?")
    app.button(key="send_message").click().run()
    assert not app.exception
    assert any("Atendente humano · rota human_reply" in str(item.value) for item in app.caption)
    assert assistant.posts == ["send_message"]
    app.run()
    assert assistant.posts == ["send_message"]


# --- HTTP clients ------------------------------------------------------------------------------------------------------


def test_password_grant_follows_the_contract_and_rfc6749_errors():
    observed = []

    def handle(request):
        observed.append(request)
        if request.url.path == "/v1/auth/revoke":
            return httpx.Response(204)
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        if form["password"] == "bad":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Invalid credentials."})
        if form["password"] == "many":
            return httpx.Response(429, json={"error": "invalid_request"}, headers={"Retry-After": "30"})
        return httpx.Response(200, json={"access_token": make_token(), "token_type": "Bearer", "expires_in": 3600})

    client = APIClient("https://backend.invalid", transport=httpx.MockTransport(handle))
    session = client.login("op_ana", PASSWORD)
    form = dict(urllib.parse.parse_qsl(observed[0].content.decode()))
    assert observed[0].url.path == "/v1/auth/token"
    assert observed[0].headers["content-type"] == "application/x-www-form-urlencoded"
    assert form == {"grant_type": "password", "username": "op_ana", "password": PASSWORD,
                    "client_id": "merchant-assistant-web"}
    assert token_claims(session["access_token"])["role"] == "operator"
    client.logout()
    assert observed[1].url.path == "/v1/auth/revoke" and observed[1].headers["authorization"].startswith("Bearer ")
    with pytest.raises(APIError) as bad:
        client.login("op_ana", "bad")
    assert (bad.value.status_code, bad.value.code) == (400, "invalid_grant")
    with pytest.raises(APIError) as many:
        client.login("op_ana", "many")
    assert many.value.code == "too_many_attempts" and "30" in str(many.value)
    assert session_ended(APIError(401, "token_required", "x")) and not session_ended(APIError(403, "x", "y"))
    assert token_claims("not-a-jwt") == {}
    client.close()


def test_ticketing_client_routes_bearer_body_and_errors():
    observed = []

    def handle(request):
        observed.append(request)
        if request.url.path.endswith("/solve"):
            return httpx.Response(409, json={"error": {"code": "ticket_solved", "message": "Solved."}})
        if request.method == "GET" and request.url.path == "/tickets":
            return httpx.Response(200, json={"tickets": []})
        return httpx.Response(202, json={"ticket": {}, "delivery": {"status": "pending"}})

    client = TicketingClient("http://127.0.0.1:8002", token="tok", transport=httpx.MockTransport(handle))
    assert client.tickets("open") == []
    assert client.act("tkt_1", "reply", "texto", "web-key-0001")["delivery"]["status"] == "pending"
    client.assign("tkt_1")
    with pytest.raises(APIError) as solved:
        client.solve("tkt_1")
    assert (solved.value.status_code, solved.value.code) == (409, "ticket_solved")
    assert [(r.method, r.url.path) for r in observed] == [
        ("GET", "/tickets"), ("POST", "/tickets/tkt_1/actions"), ("POST", "/tickets/tkt_1/assign"),
        ("POST", "/tickets/tkt_1/solve")]
    assert observed[0].url.params["status"] == "open"
    assert json.loads(observed[1].content) == {"action": "reply", "text": "texto", "idempotency_key": "web-key-0001"}
    assert all(r.headers["authorization"] == "Bearer tok" for r in observed)
    client.close()


# --- round 2 (P3 of the review of task 67) -------------------------------------------------------------------------


def test_unconfirmed_revocation_is_announced(wire):
    """P3-1: the session ends locally and the screen says the backend did not confirm the revocation."""

    class Unreachable(FakeAssistant):
        def logout(self):
            raise httpx.ConnectError("backend down")

    wire(Unreachable("merchant", "merchant:cliente1988"), FakeTicketing())
    app = login(real_app(), "cliente1988")
    app.button(key="logout").click().run()
    assert app.session_state["api_token"] is None and not app.tabs
    assert any("não confirmou a revogação" in str(item.value) for item in app.sidebar.warning)


def test_repeating_an_action_after_a_pending_send_reuses_the_key(wire):
    """P3-2: after a 202 ("envio pendente"), clicking again resends the same idempotency key."""
    ticketing = FakeTicketing(ticket("tkt_00000p", "open"))
    ticketing.fail["act_pending"] = [True]
    app = operator_app(wire, ticketing)
    app.radio(key="ticket_status").set_value("open").run()
    app.radio(key="ticket_action").set_value("approve").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert any("Envio pendente" in str(item.value) for item in app.warning)
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    first, second = ticketing.posts[-2], ticketing.posts[-1]
    assert first[0] == second[0] == "act" and first[4] == second[4]
    assert not app.exception


def test_401_on_a_ticketing_post_ends_the_session(wire):
    """P3-3: a POST answered with 401 invalid_token returns to the login with the expiry message."""
    ticketing = FakeTicketing(ticket("tkt_00000q", "open"))
    ticketing.fail["act"] = [APIError(401, "invalid_token", "Token expired.")]
    app = operator_app(wire, ticketing)
    app.radio(key="ticket_status").set_value("open").run()
    app.radio(key="ticket_action").set_value("approve").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()  # the fake's reads never answer 401: only the POST can end it
    assert ticketing.posts[-1][0] == "act"
    assert app.session_state["api_token"] is None and not app.tabs
    assert any("expirou" in str(item.value) for item in app.sidebar.warning)


def _text_ticketing(monkeypatch, handler):
    client = TicketingClient("http://127.0.0.1:8002", token="tok", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("getnet_web.ticketing.get_ticketing_client", lambda *_args, **_kwargs: client)
    return client


def test_non_json_2xx_from_the_ticketing_does_not_break_the_screen(wire, monkeypatch):
    """P3-4: a 2xx outside the contract becomes an error with a recovery action, on reads and on acts."""
    wire(FakeAssistant())
    posts = []

    def handler(request):
        if request.method == "GET" and request.url.path == "/tickets":
            return httpx.Response(200, text="<html>proxy</html>")
        return httpx.Response(200, json={})

    _text_ticketing(monkeypatch, handler)
    app = login(real_app())
    assert not app.exception
    assert any("formato inesperado" in str(item.value) and "Atualizar" in str(item.value) for item in app.error)

    item = ticket("tkt_00000r", "open")

    def acting(request):
        if request.method == "GET":
            body = {"tickets": [item]} if request.url.path == "/tickets" else item
            return httpx.Response(200, json=body)
        posts.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    _text_ticketing(monkeypatch, acting)
    app.radio(key="ticket_status").set_value("open").run()
    app.radio(key="ticket_action").set_value("approve").run()
    app.checkbox(key="ticket_confirm").check().run()
    app.button(key="ticket_send").click().run()
    assert not app.exception
    assert any("formato inesperado" in str(item.value) for item in app.error)
    app.button(key="ticket_send").click().run()  # outcome unknown: the retry keeps the same key
    assert len(posts) == 2 and posts[0]["idempotency_key"] == posts[1]["idempotency_key"]


def test_ticketing_client_rejects_bodies_outside_the_contract():
    responses = iter([httpx.Response(200, text="nope"), httpx.Response(200, json=[1]), httpx.Response(200, json={})])
    client = TicketingClient(
        "http://127.0.0.1:8002", token="tok", transport=httpx.MockTransport(lambda request: next(responses))
    )
    for call in (lambda: client.tickets(), lambda: client.tickets(), lambda: client.ticket("tkt_1")):
        with pytest.raises(APIError) as error:
            call()
        assert (error.value.status_code, error.value.code) == (502, "invalid_response")
    client.close()


# --- round 3 (OpenAPI 7079920) --------------------------------------------------------------------------------------


def test_openapi_snapshot_declares_the_attendant_turn_and_the_form_token_endpoint():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    via = schema["components"]["schemas"]["MessageResponse"]["properties"]["via"]
    assert via["enum"] == ["agent", "operator"]
    token = schema["paths"]["/v1/auth/token"]["post"]["requestBody"]["content"]
    assert "application/x-www-form-urlencoded" in token


# --- round 4 (review of round 3): the real order of a delivered human reply and per-user state ----------------------

# Shapes captured by the Reviewer from the backend (386025a, task 67 round 3). The /v1/messages answer is
# {"message": {..., "via": ...}, "run": ...}; a human reply is recorded BEFORE the merchant message that delivers it
# (D8): [merchant, assistant(agent), assistant(operator), merchant].
STAMP = "2026-10-26T10:00:00-03:00"
PROTOCOL = "HO-20261026-0F1286"
HANDOFF_MESSAGE = {
    "answer": f"Encaminhei seu caso para um atendente da nossa equipe, que vai responder por aqui. Protocolo: {PROTOCOL}.",
    "status": "handoff", "route": "human_handoff", "via": "agent", "agents": ["router", "escalation", "personality"],
    "language": "pt", "sources": [], "request_id": "req_8e1af7b1de2a4308a25e",
    "resolution": {"state": "handoff", "evidence": None},
    "handoff": {"protocol": PROTOCOL, "reason": "customer_request"},
}
OPERATOR_MESSAGE = {
    "answer": "Seu repasse foi enviado hoje às 10:00.", "status": "answered", "route": "human_reply",
    "via": "operator", "agents": ["operator"], "language": "pt", "sources": [],
    "request_id": "req_25b5c17af09d47dc9b55", "resolution": {"state": "awaiting_customer", "evidence": None},
    "handoff": None,
}
OPERATOR_TURN = {
    "role": "assistant", "text": OPERATOR_MESSAGE["answer"], "route": "human_reply", "status": "answered",
    "created_at": STAMP, "via": "operator", "operator_action": "reply", "protocol": PROTOCOL,
}
OPERATOR_LABEL = f"Atendente humano · rota human_reply · ação resposta do atendente · protocolo {PROTOCOL}"


def merchant_turn(text):
    return {"role": "merchant", "text": text, "route": "human_reply", "status": None, "created_at": STAMP}


def agent_turn(text, route="support", status="answered"):
    return {"role": "assistant", "text": text, "route": route, "status": status, "created_at": STAMP, "via": "agent"}


class ScriptedChat(FakeAssistant):
    """The assistant as the real backend records a chat: each sent message lands in `turns` before the answer, so a
    timed-out send still leaves its question there. `outcomes` is consumed one per send: "timeout" (the answer is
    lost), "fixture" (an automatic answer with its run) or the `message` object to answer with."""

    def __init__(self, *args, outcomes=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.turns, self.outcomes, self.payloads, self.gets = [], list(outcomes), [], 0

    def send_message(self, payload):
        self.payloads.append(payload)
        self.posts.append("send_message")
        self.turns.append(merchant_turn(payload["message"]))
        outcome = self.outcomes.pop(0)
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if outcome == "fixture":
            result = SimulatedAPI().send_message(payload)
            self.turns.append(agent_turn(result["message"]["answer"]))
            return result
        if outcome["via"] == "agent":  # an operator turn was recorded when the operator acted, not now
            self.turns.append(agent_turn(outcome["answer"], outcome["route"], outcome["status"]))
        return {"message": outcome | {"conversation_id": payload["conversation_id"]}, "run": None}

    def conversation(self, conversation_id):
        self.gets += 1
        return {"conversation_id": conversation_id, "state": "awaiting_customer", "messages": list(self.turns)}


def send(app, text):
    app.text_area(key="message_input").set_value(text)
    app.button(key="send_message").click().run()
    assert not app.exception


def recover(app):
    app.button(key="recover_pending_conversation").click().run()
    assert not app.exception


def attendant_labels(app):
    return [str(item.value) for item in app.caption if "Atendente humano" in str(item.value)]


def test_conversation_operator_turn_shows_action_and_protocol(wire):
    # The reply to the second message was the operator's turn (D8) and the answer was lost by a timeout.
    chat = ScriptedChat("merchant", "merchant:cliente1988", outcomes=[HANDOFF_MESSAGE, "timeout"])
    wire(chat, FakeTicketing())
    app = login(real_app(), "cliente1988")
    send(app, "Quero falar com um atendente")
    assert app.session_state["backend_message_count"] == 2 and not attendant_labels(app)
    chat.turns.append(OPERATOR_TURN)  # the operator acted meanwhile: recorded, not yet delivered
    send(app, "E aí?")
    assert [turn["role"] for turn in chat.turns] == ["merchant", "assistant", "assistant", "merchant"]  # real order
    assert app.session_state["pending_message"]["baseline_message_count"] == 2

    recover(app)
    assert chat.gets == 1 and chat.posts == ["send_message", "send_message"]
    assert app.session_state["pending_message"] is None and app.session_state["backend_message_count"] == 4
    assert app.session_state["chat_history"][-2:] == [
        {"role": "user", "text": "E aí?"},
        {"role": "assistant", "text": OPERATOR_TURN["text"], "via": "operator", "operator_action": "reply",
         "protocol": PROTOCOL},
    ]
    assert attendant_labels(app) == [OPERATOR_LABEL]
    app.run()
    assert chat.gets == 1 and len(chat.posts) == 2  # a rerun neither reads nor sends again


def test_recovery_does_not_show_a_delivered_operator_turn_twice(wire):
    chat = ScriptedChat(
        "merchant", "merchant:cliente1988", outcomes=[HANDOFF_MESSAGE, OPERATOR_MESSAGE, "timeout"]
    )
    wire(chat, FakeTicketing())
    app = login(real_app(), "cliente1988")
    send(app, "Quero falar com um atendente")
    chat.turns.append(OPERATOR_TURN)
    send(app, "E aí?")  # D8 on the send path: the answer is the operator turn recorded before the question
    assert attendant_labels(app) == ["Atendente humano · rota human_reply"]  # the message has no action or protocol
    assert app.session_state["backend_message_count"] == 4

    send(app, "Mais alguma coisa?")  # lost by a timeout, and nothing answers it yet
    assert app.session_state["pending_message"]["baseline_message_count"] == 4
    shown = list(app.session_state["chat_history"])
    recover(app)
    assert app.session_state["pending_message"] is not None and app.session_state["chat_history"] == shown
    assert any("ainda não há resposta nova" in str(item.value).lower() for item in app.info)

    chat.turns.append(agent_turn("Posso ajudar com mais alguma coisa?"))  # answered at the backend meanwhile
    recover(app)
    assert app.session_state["pending_message"] is None and app.session_state["backend_message_count"] == 6
    assert app.session_state["chat_history"][-2:] == [
        {"role": "user", "text": "Mais alguma coisa?"},
        {"role": "assistant", "text": "Posso ajudar com mais alguma coisa?"},
    ]
    assert sum(turn.get("via") == "operator" for turn in app.session_state["chat_history"]) == 1
    assert len(attendant_labels(app)) == 1


def test_recovery_prefers_the_assistant_answer_that_follows_the_question(wire):
    chat = ScriptedChat("merchant", "merchant:cliente1988", outcomes=[HANDOFF_MESSAGE, "timeout"])
    wire(chat, FakeTicketing())
    app = login(real_app(), "cliente1988")
    send(app, "Quero falar com um atendente")
    chat.turns.append(OPERATOR_TURN)
    send(app, "E aí?")
    chat.turns.append(agent_turn("Resposta automática depois da pergunta."))
    recover(app)
    assert app.session_state["chat_history"][-1] == {
        "role": "assistant", "text": "Resposta automática depois da pergunta."
    }
    assert app.session_state["backend_message_count"] == 5 and not attendant_labels(app)


def test_recovery_without_an_operator_turn_or_an_answer_keeps_the_question_pending(wire):
    chat = ScriptedChat("merchant", "merchant:cliente1988", outcomes=[HANDOFF_MESSAGE, "timeout"])
    wire(chat, FakeTicketing())
    app = login(real_app(), "cliente1988")
    send(app, "Quero falar com um atendente")
    send(app, "E aí?")
    recover(app)
    assert app.session_state["pending_message"] is not None
    assert any("ainda não há resposta nova" in str(item.value).lower() for item in app.info)
    assert not attendant_labels(app)


CONVERSATION_STATE_EMPTY = {
    "chat_history": [], "conversation_id": None, "backend_message_count": 0, "pending_message": None,
    "case_view": None, "runs": {}, "latest_run": None, "comparison": None,
}


def merchant_a_conversation(wire, outcomes):
    chat = ScriptedChat("merchant", "merchant:cliente1988", outcomes=list(outcomes))
    wire(chat, FakeTicketing())
    app = login(real_app(), "cliente1988")
    send(app, "Pergunta da lojista A")
    if "timeout" in outcomes:
        send(app, "Segunda pergunta de A")
    app.text_area(key="compare_input").set_value("Comparar o texto da lojista A")
    app.button(key="compare_routers").click().run()
    assert not app.exception
    assert app.session_state["conversation_id"] and app.session_state["runs"] and app.session_state["case_view"]
    assert app.session_state["latest_run"] and app.session_state["chat_history"] and app.session_state["comparison"]
    return chat, app


def assert_nothing_of_a_reaches_b(app, chat, a_conversation):
    for key, empty in CONVERSATION_STATE_EMPTY.items():
        assert app.session_state[key] == empty, key
    assert not app.chat_message and "lojista A" not in texts(app)
    assert not [button for button in app.button if button.key == "recover_pending_conversation"]
    assert app.text_area(key="message_input").value == "" and app.text_area(key="compare_input").value == ""
    send(app, "Pergunta de B")
    assert chat.payloads[-1]["conversation_id"] not in {None, a_conversation}
    assert app.session_state["pending_message"] is None and app.session_state["backend_message_count"] == 2


def test_logout_then_another_principal_does_not_inherit_the_conversation(wire):
    chat, app = merchant_a_conversation(wire, ("fixture", "timeout", "fixture"))
    a_conversation = app.session_state["conversation_id"]
    assert app.session_state["pending_message"] is not None
    app.button(key="logout").click().run()
    assert app.session_state["api_token"] is None and chat.revoked == 1
    for key, empty in CONVERSATION_STATE_EMPTY.items():
        assert app.session_state[key] == empty, key  # dropped at logout, before anyone else signs in
    chat.sub = "merchant:cliente2000"
    login(app, "cliente2000")
    assert_nothing_of_a_reaches_b(app, chat, a_conversation)


@pytest.mark.parametrize("new_sub", ["merchant:cliente2000", None])
def test_expiry_then_another_principal_does_not_inherit_the_conversation(wire, new_sub):
    chat, app = merchant_a_conversation(wire, ("fixture", "timeout", "fixture"))
    a_conversation = app.session_state["conversation_id"]
    app.session_state["api_token_exp"] = time.time() - 10
    app.run()
    assert app.session_state["api_token"] is None and not app.tabs  # expired: back to the login form
    chat.sub = new_sub  # a token without `sub` cannot be proven to be the same principal
    login(app, "cliente2000")
    assert_nothing_of_a_reaches_b(app, chat, a_conversation)


def test_expiry_then_the_same_principal_keeps_the_conversation(wire):
    chat, app = merchant_a_conversation(wire, ("fixture", "fixture"))
    a_conversation = app.session_state["conversation_id"]
    history = list(app.session_state["chat_history"])
    app.session_state["api_token_exp"] = time.time() - 10
    app.run()
    assert app.session_state["api_token"] is None and not app.tabs
    login(app, "cliente1988")
    assert app.session_state["chat_history"] == history and app.session_state["conversation_id"] == a_conversation
    assert "lojista A" in texts(app)
    send(app, "Mais uma pergunta de A")
    assert chat.payloads[-1]["conversation_id"] == a_conversation
    assert app.session_state["backend_message_count"] == 4
