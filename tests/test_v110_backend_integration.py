"""Opt-in (backend v1.1.0, task 110): the interface's client reads the conversation list and the conversation by id of
the real backend, in process (scripted model, no key, no network, no cost). A merchant signs in by the login page,
hands off a conversation and an attendant replies; the list gives that conversation with its protocol and the pending
reply, the conversation by id gives the attendant's turn, and the next message that confirms that turn gets its own
answer. Operators get 403.

Skipped unless the backend package is importable (see test_oauth_backend_integration.py for how to run it)."""

from __future__ import annotations

import pytest

from getnet_web.api import APIError
from getnet_web.oauth import AuthorizationAttempt, exchange_code

from .test_oauth_backend_integration import (  # noqa: F401 (backend is a fixture)
    CLIENT_ID,
    ISSUER,
    REDIRECT_URI,
    backend,
    backend_identity,
    sign_in,
    web_client,
)

HUMAN = "Quero falar com um atendente"
REPLY = "Seu repasse foi enviado hoje às 10:00."


def test_the_client_lists_and_reads_the_merchants_latest_conversation(backend):  # noqa: F811
    browser, web = backend
    attempt = AuthorizationAttempt.create(ISSUER, REDIRECT_URI, CLIENT_ID)
    token = exchange_code(web, attempt, attempt.validate_callback(sign_in(browser, attempt)))["access_token"]
    api = web_client(browser, token)
    identity = browser.app.state.identity
    operator = {
        "Authorization": "Bearer "
        + identity.issue(backend_identity.Principal.operator("op_ana"), "test-client")["access_token"]
    }
    try:
        assert api.list_conversations() == []

        sent = browser.post("/messages", json={"message": HUMAN}, headers={"Authorization": f"Bearer {token}"})
        assert sent.status_code == 200 and sent.json()["status"] == "handoff", sent.text
        conversation_id, protocol = sent.json()["conversation_id"], sent.json()["handoff"]["protocol"]
        case = next(c for c in browser.get("/v1/handoffs", headers=operator).json()["cases"]
                    if c["protocol"] == protocol)
        body = {"action": "reply", "idempotency_key": "key-web-resume-01", "text": REPLY}
        assert browser.post(f"/v1/handoffs/{case['case_id']}/actions", headers=operator, json=body).status_code == 200

        (latest,) = api.list_conversations(limit=1)
        assert latest["conversation_id"] == conversation_id and latest["handoff_protocol"] == protocol
        assert latest["pending_operator_reply"] is True and REPLY not in str(latest)
        conversation = api.conversation(conversation_id)
        last = conversation["messages"][-1]
        assert (last["role"], last["via"], last["text"], last["protocol"]) == ("assistant", "operator", REPLY, protocol)
        assert [m["role"] for m in conversation["messages"]] == ["merchant", "assistant", "assistant"]

        # the interface confirms the reply it showed: the next message gets its own answer, not the reply again
        answered = api.send_message({
            "message": "E quando cai na conta?", "conversation_id": conversation_id, "profile_id": "reference",
            "profile_version": 1, "router_mechanism": "llm", "displayed_operator_message_id": last["message_id"],
        })["message"]
        assert answered["route"] != "human_reply" and answered["via"] == "agent" and answered["answer"] != REPLY
        assert api.list_conversations(limit=1)[0]["pending_operator_reply"] is False
        texts = [m["text"] for m in api.conversation(conversation_id)["messages"]]
        assert texts.count(REPLY) == 1 and texts[3] == "E quando cai na conta?"

        with pytest.raises(APIError) as refused:
            api.list_conversations(limit=21)
        assert (refused.value.status_code, refused.value.code) == (422, "invalid_request")
    finally:
        api.close()

    staff = web_client(browser, operator["Authorization"].removeprefix("Bearer "))
    try:
        with pytest.raises(APIError) as forbidden:
            staff.list_conversations()
        assert (forbidden.value.status_code, forbidden.value.code) == (403, "insufficient_scope")
    finally:
        staff.close()
