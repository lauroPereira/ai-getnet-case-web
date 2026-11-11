"""v1.1.0 (backend task 110, D2, CA-77): after a new login the interface resumes the merchant's latest conversation
(GET /v1/conversations, then the conversation by id), with the attendant's turn; **Nova conversa** starts another one.
Network-free: a fake assistant that keeps the conversations as the backend records them, and httpx.MockTransport for
the HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest

from getnet_web.api import APIClient, APIError
from getnet_web.generated_api import OPERATIONS
from getnet_web.simulated import SimulatedAPI

from .test_interface import ROOT
from .test_v070_interface import (  # noqa: F401 (wire is a fixture)
    HANDOFF_MESSAGE,
    OPERATOR_LABEL,
    OPERATOR_MESSAGE,
    OPERATOR_TURN,
    PROTOCOL,
    FakeTicketing,
    ScriptedChat,
    agent_turn,
    attendant_labels,
    login,
    merchant_turn,
    real_app,
    send,
    texts,
    wire,
)

RESUMED_ID = "conv_resumed0000000000001"
UPDATED_AT = "2026-11-10T09:14:31-03:00"
HANDOFF_TURNS = [
    merchant_turn("Quero falar com um atendente"),
    agent_turn(HANDOFF_MESSAGE["answer"], route="human_handoff", status="handoff"),
    OPERATOR_TURN,  # the attendant answered after the merchant left: recorded, not yet delivered
]


class ResumeChat(ScriptedChat):
    """The assistant with the merchant's conversations: `stored` maps an id to its turns, newest conversation last.
    A sent message lands in the conversation of its id. `list_error` and `conversation_error` make the reads fail.

    Delivery as the backend does it: each turn's `message_id` is its position; an attendant's turn not yet delivered
    is the answer to the next message (D8) unless that message carries its `displayed_operator_message_id` (v1.1.0),
    which marks it and the older ones as delivered so the message gets its own answer."""

    def __init__(self, *args, stored=None, list_error=None, conversation_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stored = dict(stored or {})
        self.list_error, self.conversation_error = list_error, conversation_error
        self.lists: list[int] = []
        self.reads: list[str] = []
        self.delivered: set[tuple[str, int]] = set()

    def pending(self, conversation_id):
        return [
            number for number, turn in enumerate(self.stored.get(conversation_id, []), start=1)
            if turn.get("via") == "operator" and (conversation_id, number) not in self.delivered
        ]

    def send_message(self, payload):
        conversation_id = payload["conversation_id"]
        waiting = self.pending(conversation_id)
        shown = payload.get("displayed_operator_message_id")
        if shown in waiting:
            self.delivered |= {(conversation_id, number) for number in waiting if number <= shown}
            waiting = [number for number in waiting if number > shown]
        if waiting:  # D8: the oldest reply not delivered is the answer to this message
            self.delivered.add((conversation_id, waiting[0]))
            text = self.stored[conversation_id][waiting[0] - 1]["text"]
            self.outcomes.insert(0, OPERATOR_MESSAGE | {"answer": text})
        self.turns = self.stored.pop(conversation_id, [])  # the turns of that conversation, now the newest
        answer = super().send_message(payload)
        self.stored[conversation_id] = self.turns
        return answer

    def list_conversations(self, limit=5):
        self.lists.append(limit)
        if self.list_error:
            raise self.list_error
        items = [
            {"conversation_id": conversation_id, "state": "handoff", "created_at": "2026-11-10T09:12:04-03:00",
             "updated_at": UPDATED_AT, "handoff_protocol": PROTOCOL,
             "pending_operator_reply": bool(self.pending(conversation_id))}
            for conversation_id in reversed(self.stored)
        ]
        return items[:limit]

    def conversation(self, conversation_id):
        self.reads.append(conversation_id)
        if self.conversation_error:
            raise self.conversation_error
        messages = [turn | {"message_id": number} for number, turn in enumerate(self.stored[conversation_id], start=1)]
        return {"conversation_id": conversation_id, "state": "handoff", "messages": messages}


def merchant_app(wire, chat):  # noqa: F811 (the fixture's value)
    wire(chat, FakeTicketing())
    return login(real_app(), "cliente1988")


def test_a_new_login_resumes_the_latest_conversation_with_the_attendant_turn(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture"])
    app = merchant_app(wire, chat)
    assert not app.exception
    assert chat.lists == [1] and chat.reads == [RESUMED_ID] and chat.posts == []  # GET only, no cost
    assert app.session_state["conversation_id"] == RESUMED_ID
    assert app.session_state["chat_history"] == [
        {"role": "user", "text": "Quero falar com um atendente"},
        {"role": "assistant", "text": HANDOFF_MESSAGE["answer"]},
        {"role": "assistant", "text": OPERATOR_TURN["text"], "via": "operator", "operator_action": "reply",
         "protocol": PROTOCOL},
    ]
    assert attendant_labels(app) == [OPERATOR_LABEL]
    assert app.session_state["backend_message_count"] == 3
    assert "Conversa retomada · última atividade em 10/11/2026 09:14" in [str(item.value) for item in app.info]
    assert "próxima mensagem" not in texts(app)  # no notice about the reply coming back: it does not
    assert f"protocolo {PROTOCOL}" in texts(app)  # the case line shows the protocol of the conversation's case

    app.run()  # a rerun reads nothing again
    assert chat.lists == [1] and chat.reads == [RESUMED_ID]

    send(app, "Obrigado pela resposta")
    assert chat.payloads[-1]["conversation_id"] == RESUMED_ID  # the next message goes to the resumed conversation
    assert chat.payloads[-1]["displayed_operator_message_id"] == 3  # the attendant's turn already on screen
    assert app.session_state["conversation_id"] == RESUMED_ID
    assert not any("Conversa retomada" in str(item.value) for item in app.info)  # the notice lasts until then


def test_the_next_message_gets_its_own_answer_and_the_reply_shows_once(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture", "fixture"])
    app = merchant_app(wire, chat)
    send(app, "E quando cai na conta?")
    history = app.session_state["chat_history"]
    assert [turn["text"] for turn in history].count(OPERATOR_TURN["text"]) == 1
    assert history[-2] == {"role": "user", "text": "E quando cai na conta?"}
    assert history[-1]["role"] == "assistant" and "via" not in history[-1]  # the assistant's answer to the question
    assert attendant_labels(app) == [OPERATOR_LABEL]
    recorded = chat.conversation(RESUMED_ID)["messages"]  # the screen matches the history the backend now records
    assert [(turn["role"], turn["text"]) for turn in history] == [
        ("user" if turn["role"] == "merchant" else "assistant", turn["text"]) for turn in recorded
    ]

    send(app, "Obrigado")  # only the first message after the resume confirms the reply
    assert "displayed_operator_message_id" not in chat.payloads[-1]


def test_a_reply_already_delivered_is_not_confirmed_again(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988",
                      stored={RESUMED_ID: [*HANDOFF_TURNS, merchant_turn("Alguma novidade?")]}, outcomes=["fixture"])
    chat.delivered.add((RESUMED_ID, 3))  # delivered by "Alguma novidade?" before the merchant left
    app = merchant_app(wire, chat)
    assert app.session_state["conversation_id"] == RESUMED_ID
    send(app, "Obrigado")
    assert "displayed_operator_message_id" not in chat.payloads[-1]


def test_a_reply_written_after_the_resume_is_still_delivered_once(wire):  # noqa: F811
    """Race: the attendant writes again after the interface loaded the history. The reply on screen counts as
    delivered; the newer one, never shown, is the answer to this message and appears once."""
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture"])
    app = merchant_app(wire, chat)
    newer = OPERATOR_TURN | {"text": "Complementando: o valor já foi conciliado."}
    chat.stored[RESUMED_ID].append(newer)
    send(app, "Obrigado")
    assert chat.payloads[-1]["displayed_operator_message_id"] == 3
    texts_on_screen = [turn["text"] for turn in app.session_state["chat_history"]]
    assert texts_on_screen.count(OPERATOR_TURN["text"]) == 1 and texts_on_screen.count(newer["text"]) == 1
    assert app.session_state["chat_history"][-1]["via"] == "operator"
    assert chat.outcomes == ["fixture"]  # the merchant's own answer was not produced: the newer reply was


def test_reloading_the_page_brings_back_the_conversation_and_the_reply_written_meanwhile(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", outcomes=[HANDOFF_MESSAGE])
    first = merchant_app(wire, chat)
    assert chat.lists == [1] and chat.reads == []  # nothing to resume: as before
    send(first, "Quero falar com um atendente")
    conversation_id = first.session_state["conversation_id"]
    chat.stored[conversation_id].append(OPERATOR_TURN)  # the attendant replies while the merchant is away

    second = login(real_app(), "cliente1988")  # a reloaded page is a new Streamlit session
    assert not second.exception
    assert second.session_state["conversation_id"] == conversation_id
    assert [turn["role"] for turn in second.session_state["chat_history"]] == ["user", "assistant", "assistant"]
    assert second.session_state["chat_history"][-1]["via"] == "operator"
    assert attendant_labels(second) == [OPERATOR_LABEL]


def test_new_conversation_starts_another_and_keeps_the_runs_of_the_session(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture", "fixture"])
    app = merchant_app(wire, chat)
    send(app, "Quando recebo minhas vendas?")
    runs = dict(app.session_state["runs"])
    assert runs and app.session_state["conversation_id"] == RESUMED_ID

    app.button(key="new_conversation").click().run()
    assert not app.exception
    assert app.session_state["chat_history"] == [] and app.session_state["conversation_id"] is None
    assert app.session_state["case_view"] is None and app.session_state["backend_message_count"] == 0
    assert app.session_state["runs"] == runs  # Resultados keeps the runs of this session
    assert not app.chat_message and not [button for button in app.button if button.key == "new_conversation"]
    assert chat.lists == [1] and chat.posts == ["send_message"]  # nothing deleted, nothing resumed again
    app.run()
    assert app.session_state["chat_history"] == [] and chat.lists == [1]

    send(app, "Outra dúvida")
    assert chat.payloads[-1]["conversation_id"] not in {None, RESUMED_ID}
    assert app.session_state["chat_history"][0] == {"role": "user", "text": "Outra dúvida"}


def test_an_empty_list_keeps_the_current_behaviour(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", outcomes=["fixture"])
    app = merchant_app(wire, chat)
    assert not app.exception and chat.lists == [1] and chat.reads == []
    assert app.session_state["chat_history"] == [] and app.session_state["conversation_id"] is None
    assert "Conversa retomada" not in texts(app) and not app.warning
    assert not [button for button in app.button if button.key == "new_conversation"]
    send(app, "Primeira pergunta")
    assert chat.payloads[-1]["conversation_id"].startswith("conv_")


@pytest.mark.parametrize(
    "failure",
    [
        {"list_error": APIError(503, "service_unavailable", "Backend unavailable.")},
        {"list_error": httpx.ConnectError("synthetic")},
        {"conversation_error": APIError(404, "not_found", "Conversation not found.")},
        {"conversation_error": httpx.ReadTimeout("synthetic")},
    ],
)
def test_a_failed_resume_warns_briefly_and_starts_a_new_conversation(wire, failure):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture"], **failure)
    app = merchant_app(wire, chat)
    assert not app.exception and [tab.label for tab in app.tabs] == ["Atendimento", "Resultados"]
    assert [str(item.value) for item in app.warning] == [
        "Não foi possível retomar a última conversa. Uma conversa nova foi iniciada."
    ]
    assert app.session_state["chat_history"] == [] and app.session_state["conversation_id"] is None
    app.run()
    assert not app.warning and chat.lists == [1]  # said once, never retried by a rerun
    send(app, "Pergunta depois da falha")
    assert chat.payloads[-1]["conversation_id"] not in {None, RESUMED_ID}


@pytest.mark.parametrize("status", [404, 405])
def test_a_backend_without_the_list_means_nothing_to_resume_without_a_notice(wire, status):  # noqa: F811
    """A backend before v1.1.0 answers the list with 404 (or 405): there is nothing to resume, not a failure."""
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture"], list_error=APIError(status, "http_error", "Not Found"))
    app = merchant_app(wire, chat)
    assert not app.exception and not app.warning and chat.lists == [1] and chat.reads == []
    assert app.session_state["chat_history"] == [] and app.session_state["conversation_id"] is None
    send(app, "Primeira pergunta")
    assert chat.payloads[-1]["conversation_id"] not in {None, RESUMED_ID}
    assert "displayed_operator_message_id" not in chat.payloads[-1]


def test_a_401_on_the_list_ends_the_session(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", list_error=APIError(401, "invalid_token", "Revoked."))
    wire(chat, FakeTicketing())
    app = real_app()
    login(app, "cliente1988")
    assert app.session_state["api_token"] is None and not app.tabs


@pytest.mark.parametrize(("role", "sub"), [("admin", "admin:default"), ("demo", "demo:default")])
def test_only_merchant_and_demo_sessions_resume(wire, role, sub):  # noqa: F811
    chat = ResumeChat(role, sub, stored={RESUMED_ID: list(HANDOFF_TURNS)})
    app = merchant_app(wire, chat)
    assert not app.exception
    resumed = role == "demo"
    assert chat.lists == ([1] if resumed else [])
    assert (app.session_state["conversation_id"] == RESUMED_ID) is resumed


def test_logout_and_another_login_resume_again_for_the_new_principal(wire):  # noqa: F811
    chat = ResumeChat("merchant", "merchant:cliente1988", stored={RESUMED_ID: list(HANDOFF_TURNS)},
                      outcomes=["fixture"])
    app = merchant_app(wire, chat)
    app.button(key="new_conversation").click().run()
    app.button(key="logout").click().run()
    assert app.session_state["api_token"] is None and app.session_state["resume_checked"] is False
    chat.sub = "merchant:cliente2000"
    chat.stored = {}  # the backend lists only the token's merchant's conversations: B has none
    login(app, "cliente2000")
    assert chat.lists == [1, 1] and app.session_state["chat_history"] == []
    assert app.session_state["conversation_id"] is None


def test_simulated_mode_has_no_earlier_conversation():
    assert SimulatedAPI().list_conversations() == []


# --- HTTP client and contract snapshot --------------------------------------------------------------------------------


def test_http_client_lists_conversations_with_the_limit_and_the_bearer():
    observed = []
    item = {"conversation_id": RESUMED_ID, "state": "handoff", "created_at": UPDATED_AT, "updated_at": UPDATED_AT,
            "handoff_protocol": PROTOCOL, "pending_operator_reply": True}

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={"conversations": [item]})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    assert client.list_conversations(limit=1) == [item]
    assert (observed[0].method, observed[0].url.path, dict(observed[0].url.params)) == (
        "GET", "/v1/conversations", {"limit": "1"}
    )
    assert observed[0].headers["Authorization"] == "Bearer synthetic-session"
    client.close()


def test_http_client_raises_the_structured_refusal_of_the_list():
    def handle(_request):
        return httpx.Response(403, json={"error": {"code": "insufficient_scope", "message": "No merchant."}})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    with pytest.raises(APIError) as error:
        client.list_conversations()
    assert (error.value.status_code, error.value.code) == (403, "insufficient_scope")
    client.close()


def test_openapi_snapshot_declares_the_conversation_list():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    route = schema["paths"]["/v1/conversations"]["get"]
    assert route["security"] == [{"SessionBearer": []}]
    limit = next(p for p in route["parameters"] if p["name"] == "limit")
    assert (limit["schema"]["minimum"], limit["schema"]["maximum"], limit["schema"]["default"]) == (1, 20, 5)
    item = schema["components"]["schemas"]["ConversationSummary"]["properties"]
    assert set(item) == {
        "conversation_id", "state", "created_at", "updated_at", "handoff_protocol", "pending_operator_reply"
    }
    assert OPERATIONS["list_conversations"] == ("GET", "/v1/conversations")
    assert schema["info"]["version"] == "1.1.0"


def test_openapi_snapshot_declares_the_displayed_reply_confirmation():
    request = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))["components"]["schemas"][
        "V1MessageRequest"
    ]
    field = request["properties"]["displayed_operator_message_id"]
    assert {"type": "integer", "minimum": 1} in field["anyOf"] and {"type": "null"} in field["anyOf"]
    assert "displayed_operator_message_id" not in request["required"]


def test_http_client_sends_the_confirmation_in_the_message_body():
    observed = []

    def handle(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"answer": "ok"}, "run": None})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    client.send_message({"message": "Oi", "conversation_id": RESUMED_ID, "displayed_operator_message_id": 3})
    assert observed[0]["displayed_operator_message_id"] == 3
    client.close()
