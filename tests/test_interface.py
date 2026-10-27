from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from getnet_web.api import APIClient, APIError, close_session_client, get_session_client
from getnet_web.generated_api import OPERATIONS
from getnet_web.simulated import SimulatedAPI

ROOT = Path(__file__).resolve().parents[1]


def _conversation_recovery_fixture():
    return json.loads((ROOT / "fixtures" / "conversation-recovery-r2.json").read_text(encoding="utf-8"))


def test_generated_client_operations_match_openapi_v1_contract():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    operations = {
        details["operationId"]: (method.upper(), path)
        for path, path_item in schema["paths"].items()
        if path.startswith("/v1/")
        for method, details in path_item.items()
        if method in {"get", "post", "put", "delete"}
    }
    assert len(OPERATIONS) == 22
    for method, path in OPERATIONS.values():
        assert (method, path) in operations.values()


def test_http_client_sends_only_backend_bearer_token_and_uses_v1():
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={"profiles": []})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    assert client.profiles() == []
    assert observed[0].url.path == "/v1/profiles"
    assert observed[0].headers["Authorization"] == "Bearer synthetic-session"
    assert b"OPENAI_API_KEY" not in observed[0].content
    client.close()


def test_http_client_reads_the_backend_parameter_catalog():
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={"models": [{"model": "gpt-6-luna"}], "verified_at": "2026-10-27"})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    assert client.model_catalog()["models"][0]["model"] == "gpt-6-luna"
    assert observed[0].method == "GET"
    assert observed[0].url.path == "/v1/catalog/models"
    client.close()


def test_openapi_documents_session_bearer_for_protected_operations():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    assert schema["components"]["securitySchemes"]["SessionBearer"] == {"type": "http", "scheme": "bearer"}
    assert schema["paths"]["/v1/profiles"]["get"]["security"] == [{"SessionBearer": []}]
    assert "security" not in schema["paths"]["/v1/sessions"]["post"]


def test_openapi_documents_router_budget_error_body():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    responses = schema["paths"]["/v1/router/compare"]["post"]["responses"]
    assert {"201", "422", "429"}.issubset(responses)
    assert responses["429"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorBody"}
    assert "ErrorDetail" in schema["components"]["schemas"]


def test_http_client_propagates_structured_budget_rejection():
    def handle(_request):
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "request_budget_exceeded",
                    "message": "Router comparison was blocked by the configured budget.",
                    "request_id": "req_synthetic_budget",
                }
            },
        )

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    with pytest.raises(APIError) as error:
        client.compare({"message": "synthetic"})
    assert error.value.status_code == 429
    assert error.value.code == "request_budget_exceeded"
    assert str(error.value) == "Router comparison was blocked by the configured budget."
    client.close()


def test_session_http_client_is_reused_and_closed_on_logout_or_identity_change():
    class FakeClient:
        def __init__(self, base_url, *, token):
            self.identity = (base_url, token)
            self.closed = False

        def close(self):
            self.closed = True

    state = {}
    first = get_session_client(state, "http://backend/", "session-a", factory=FakeClient)
    assert get_session_client(state, "http://backend", "session-a", factory=FakeClient) is first
    second = get_session_client(state, "http://backend", "session-b", factory=FakeClient)
    assert first.closed and second is not first
    close_session_client(state)
    assert second.closed and state["_getnet_api_client"] is None


def test_http_client_updates_a_draft_through_the_generated_operation():
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={"profile_id": "adjusted", "version": 2, "state": "draft"})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    result = client.update_profile("adjusted", {"expected_version": 1, "agents": {"router": "synthetic"}})
    assert result["version"] == 2
    assert observed[0].method == "PUT"
    assert observed[0].url.path == "/v1/profiles/adjusted/draft"
    assert observed[0].headers["Authorization"] == "Bearer synthetic-session"
    client.close()


def test_kb_client_uses_documented_routes_only_after_explicit_calls():
    observed = []

    def handle(request):
        observed.append(request)
        if request.url.path == "/v1/kb/documents":
            return httpx.Response(200, json={"documents": []})
        return httpx.Response(200, json={"digest": "synthetic-digest", "estimated_cost_usd": 0.0})

    client = APIClient("https://backend.invalid", token="synthetic-session", transport=httpx.MockTransport(handle))
    assert client.kb_documents() == []
    estimate = client.kb_estimate({"demo:guide": 1})
    assert estimate["digest"] == "synthetic-digest"
    assert [request.url.path for request in observed] == ["/v1/kb/documents", "/v1/kb/versions/estimate"]
    assert observed[1].method == "POST" and b'"revisions"' in observed[1].content
    assert all(request.headers["Authorization"] == "Bearer synthetic-session" for request in observed)
    client.close()


def test_simulated_and_recorded_modes_are_distinct_and_consume_comparison_once():
    api = SimulatedAPI()
    comparison = api.compare({"selected_mechanism": "jev"})
    payload = {
        "message": "mensagem sintética",
        "profile_id": "reference",
        "profile_version": 1,
        "router_mechanism": "jev",
        "comparison_id": comparison["comparison_id"],
    }
    first = api.send_message(payload)
    assert first["run"]["execution_mode"] == "simulated"
    assert first["message"]["answer"].startswith("[Modo simulated]")
    assert api.run(first["message"]["request_id"])["execution_mode"] == "simulated"
    with pytest.raises(APIError) as error:
        api.send_message(payload)
    assert error.value.status_code == 409

    recorded_api = SimulatedAPI(recorded=True)
    recorded = recorded_api.send_message(
        {"message": "pergunta", "profile_id": "reference", "profile_version": 1, "router_mechanism": "llm"}
    )
    assert recorded["run"]["execution_mode"] == "recorded"
    assert recorded["message"]["answer"].startswith("[Modo recorded]")
    assert recorded_api.run(recorded["message"]["request_id"])["execution_mode"] == "recorded"

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()
    app.selectbox(key="mode").select("Gravado").run()
    app.text_area(key="message_input").set_value("pergunta sintética gravada")
    app.button(key="send_message").click().run()
    assert not app.exception
    assert any("[Modo recorded]" in str(item.value) for item in app.markdown)
    assert app.session_state["latest_run"]["execution_mode"] == "recorded"


def test_simulated_model_catalog_exposes_only_documented_safe_parameter_choices():
    catalog = SimulatedAPI().model_catalog()
    openai = next(item for item in catalog["models"] if item["provider"] == "openai")
    parameter = openai["parameters"]["max_output_tokens"]
    assert parameter["minimum"] == 16
    assert parameter["maximum_by_agent"]["personality"] == 600
    assert "evaluation" not in parameter["maximum_by_agent"]


def test_app_rerun_does_not_resubmit_a_message():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()
    assert not app.exception
    app.text_area(key="message_input").set_value("mensagem de teste sintética")
    app.button(key="send_message").click().run()
    assert not app.exception
    assert sum("Recebi sua mensagem" in str(item.value) for item in app.markdown) == 1


@pytest.mark.parametrize(("initial", "changed"), [("llm", "jev"), ("jev", "llm")])
def test_changing_comparison_selection_invalidates_result_until_explicit_recompare(monkeypatch, initial, changed):
    comparisons = []
    messages = []
    original_compare = SimulatedAPI.compare
    original_send = SimulatedAPI.send_message

    def track_compare(self, payload):
        result = original_compare(self, payload)
        comparisons.append((payload["selected_mechanism"], result["comparison_id"]))
        return result

    def track_send(self, payload):
        messages.append(dict(payload))
        return original_send(self, payload)

    monkeypatch.setattr(SimulatedAPI, "compare", track_compare)
    monkeypatch.setattr(SimulatedAPI, "send_message", track_send)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()
    app.radio(key="comparison_selection").set_value(initial)
    app.text_area(key="compare_input").set_value("mensagem sintética para comparar")
    app.button(key="compare_routers").click().run()
    assert not app.exception
    original_id = app.session_state["comparison"]["comparison_id"]
    assert comparisons == [(initial, original_id)]
    assert f"seleção: {initial.upper()}" in " ".join(str(item.value) for item in app.caption)

    app.run()
    assert len(comparisons) == 1
    app.radio(key="comparison_selection").set_value(changed).run()
    assert not app.exception
    assert app.session_state["comparison"] is None
    assert len(comparisons) == 1
    assert any("resultado anterior foi invalidado" in str(item.value) for item in app.info)
    assert not any(button.label == "Usar esta decisão e iniciar atendimento" for button in app.button)

    app.run()
    assert len(comparisons) == 1
    app.button(key="compare_routers").click().run()
    assert not app.exception
    new_id = app.session_state["comparison"]["comparison_id"]
    assert new_id != original_id
    assert comparisons == [(initial, original_id), (changed, new_id)]
    assert f"seleção: {changed.upper()}" in " ".join(str(item.value) for item in app.caption)

    app.button(key="use_comparison").click().run()
    assert not app.exception
    assert len(comparisons) == 2
    assert app.session_state["comparison"] is None
    assert app.session_state["latest_run"]["router_mechanism"] == changed
    assert len(messages) == 1
    assert messages[0]["router_mechanism"] == changed
    assert messages[0]["comparison_id"] == new_id
    app.run()
    assert sum("Recebi sua mensagem" in str(item.value) for item in app.markdown) == 1


def test_admin_ui_offers_documented_output_token_limit():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()
    assert not app.exception
    app.selectbox(key="sim_role").select("admin").run()
    assert not app.exception
    assert app.checkbox(key="create_reference_router_output_enabled").value is False
    app.checkbox(key="create_reference_router_output_enabled").set_value(True).run()
    assert not app.exception
    assert app.number_input(key="create_reference_router_output_tokens").min == 16
    assert app.number_input(key="create_reference_router_output_tokens").max == 300


def test_kb_confirmation_controls_enable_once_without_form_submission(monkeypatch):
    class FakeAdminAPI:
        execution_mode = "real"

        def __init__(self):
            self.actions = {"estimate": 0, "build": 0, "test": 0, "activate": 0, "rollback": 0}
            self.active_version_id = "kb_initial"
            self.previous_version_id = "kb_previous"
            self.candidate = {
                "version_id": "kb_candidate_a",
                "state": "ready",
                "test_result": {"passed": False},
            }

        def profiles(self):
            return SimulatedAPI().profiles()

        def model_catalog(self):
            return SimulatedAPI().model_catalog()

        def kb_documents(self):
            return [
                {
                    "document_id": "demo:guide",
                    "title": "Synthetic guide",
                    "source_kind": "simulated_internal",
                    "editable": True,
                    "draft_revision": 2,
                }
            ]

        def kb_versions(self):
            return {
                "active_version_id": self.active_version_id,
                "previous_version_id": self.previous_version_id,
                "versions": [dict(self.candidate)],
            }

        def kb_document(self, document_id):
            assert document_id == "demo:guide"
            return {
                "document_id": document_id,
                "source_kind": "simulated_internal",
                "content": "Published synthetic content",
                "content_hash": "a" * 64,
                "draft": {"revision": 2, "content": "Synthetic draft"},
            }

        def kb_diff(self, document_id):
            assert document_id == "demo:guide"
            return {"diff": "- old\\n+ synthetic"}

        def kb_estimate(self, revisions):
            self.actions["estimate"] += 1
            return {
                "digest": "synthetic-digest",
                "documents": 1,
                "chunks": 3,
                "embedding_model": "synthetic-embedder",
                "estimated_cost_usd": 0.002,
                "active_version_id": self.active_version_id,
                "revisions": revisions,
            }

        def kb_build(self, payload):
            self.actions["build"] += 1
            assert payload["confirmed"] is True
            assert payload["max_cost_usd"] == pytest.approx(0.125)
            return {"version_id": "kb_candidate_b", "state": "testing"}

        def kb_test(self, version_id, payload):
            self.actions["test"] += 1
            assert version_id == self.candidate["version_id"]
            assert payload["confirmed"] is True
            assert payload["max_cost_usd"] == pytest.approx(0.11)
            self.candidate["test_result"] = {"passed": True}
            self.candidate["state"] = "ready"
            return {"passed": True}

        def kb_activate(self, version_id, payload):
            self.actions["activate"] += 1
            assert version_id == self.candidate["version_id"]
            assert payload == {"expected_active_version_id": "kb_initial", "confirmed": True}
            self.previous_version_id, self.active_version_id = self.active_version_id, version_id
            self.candidate["state"] = "active"

        def kb_rollback(self, version_id, payload):
            self.actions["rollback"] += 1
            assert version_id == "kb_initial"
            assert payload == {"expected_active_version_id": "kb_candidate_a", "confirmed": True}
            self.previous_version_id, self.active_version_id = self.active_version_id, version_id

    api = FakeAdminAPI()
    monkeypatch.setattr("getnet_web.api.get_session_client", lambda *_args, **_kwargs: api)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10)
    app.session_state["api_token"] = "synthetic-session"
    app.session_state["api_role"] = "admin"
    app.run()
    app.selectbox(key="mode").select("Real (API)").run()
    assert not app.exception

    app.button(key="kb_estimate_action").click().run()
    assert api.actions == {"estimate": 1, "build": 0, "test": 0, "activate": 0, "rollback": 0}

    app.number_input(key="kb_build_max_cost").set_value(0.125).run()
    assert app.button(key="kb_build_action").disabled
    app.checkbox(key="kb_confirm_build").set_value(True).run()
    assert not app.exception
    assert not app.button(key="kb_build_action").disabled
    assert app.number_input(key="kb_build_max_cost").value == pytest.approx(0.125)
    assert api.actions["build"] == 0
    app.number_input(key="kb_build_max_cost").set_value(0.5).run()
    assert app.checkbox(key="kb_confirm_build").value is False
    assert app.button(key="kb_build_action").disabled
    assert app.number_input(key="kb_build_max_cost").value == pytest.approx(0.5)
    assert api.actions["build"] == 0
    app.number_input(key="kb_build_max_cost").set_value(0.125).run()
    assert app.checkbox(key="kb_confirm_build").value is False
    assert app.button(key="kb_build_action").disabled
    assert app.number_input(key="kb_build_max_cost").value == pytest.approx(0.125)
    app.checkbox(key="kb_confirm_build").set_value(True).run()
    assert not app.button(key="kb_build_action").disabled
    app.run()
    assert api.actions["build"] == 0
    app.button(key="kb_build_action").click().run()
    assert api.actions["build"] == 1

    assert app.button(key="kb_test_action").disabled is True
    app.number_input(key="kb_test_max_cost").set_value(0.11).run()
    app.checkbox(key="kb_confirm_test").set_value(True).run()
    assert not app.exception
    assert app.number_input(key="kb_test_max_cost").value == pytest.approx(0.11)
    assert not app.button(key="kb_test_action").disabled
    assert api.actions["test"] == 0
    app.run()
    assert api.actions["test"] == 0
    app.button(key="kb_test_action").click().run()
    assert api.actions["test"] == 1

    assert app.button(key="kb_activate_action").disabled is True
    app.checkbox(key="kb_confirm_activate").set_value(True).run()
    assert not app.exception
    assert not app.button(key="kb_activate_action").disabled
    assert api.actions["activate"] == 0
    app.run()
    assert api.actions["activate"] == 0
    app.button(key="kb_activate_action").click().run()
    assert api.actions["activate"] == 1

    app.checkbox(key="kb_confirm_rollback").set_value(True).run()
    assert not app.exception
    assert not app.button(key="kb_rollback_action").disabled
    assert api.actions["rollback"] == 0
    app.run()
    assert api.actions["rollback"] == 0
    app.button(key="kb_rollback_action").click().run()
    assert api.actions["rollback"] == 1


def test_transport_failure_logs_only_sanitized_http_measurements(caplog):
    def handle(request):
        raise httpx.ReadTimeout("private request text and token", request=request)

    client = APIClient(
        "https://backend.invalid",
        token="synthetic-secret-token",
        transport=httpx.MockTransport(handle),
    )
    with caplog.at_level("WARNING", logger="getnet_web.http"), pytest.raises(httpx.ReadTimeout):
        client.send_message(
            {
                "message": "private synthetic question",
                "profile_id": "reference",
                "profile_version": 1,
                "router_mechanism": "llm",
            }
        )
    event = next(record.event for record in caplog.records if record.name == "getnet_web.http")
    assert event["operation"] == "send_v1_message"
    assert event["duration_ms"] >= 0
    assert event["error_class"] == "ReadTimeout"
    assert "private request text" not in caplog.text
    assert "synthetic-secret-token" not in caplog.text
    assert "private synthetic question" not in caplog.text
    client.close()


def _real_app_with_fake_chat_api(
    monkeypatch, api, *, conversation_id=None, chat_history=None, backend_message_count=0
):
    monkeypatch.setattr("getnet_web.api.get_session_client", lambda *_args, **_kwargs: api)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10)
    app.session_state["api_token"] = "synthetic-session"
    app.session_state["api_role"] = "demo"
    app.session_state["conversation_id"] = conversation_id
    app.session_state["backend_message_count"] = backend_message_count
    if chat_history is not None:
        app.session_state["chat_history"] = [dict(item) for item in chat_history]
    app.run()
    app.selectbox(key="mode").select("Real (API)").run()
    assert not app.exception
    return app


def test_first_send_id_survives_transport_failure_and_manual_get_recovers(monkeypatch):
    class FakeChatAPI:
        execution_mode = "real"

        def __init__(self):
            self.posts = []
            self.gets = []
            self.pending_question = None

        def profiles(self):
            return SimulatedAPI().profiles()

        def send_message(self, payload):
            self.posts.append(dict(payload))
            self.pending_question = payload["message"]
            raise httpx.ReadTimeout("synthetic timeout")

        def conversation(self, conversation_id):
            self.gets.append(conversation_id)
            if len(self.gets) == 1:
                raise APIError(404, "not_found", "Conversation not found.")
            if len(self.gets) == 2:
                body = _conversation_recovery_fixture()
                return {**body, "messages": []}
            return _conversation_recovery_fixture()

    api = FakeChatAPI()
    simulated_history = [
        {"role": "user", "text": "Pergunta anterior no modo simulado"},
        {"role": "assistant", "text": "Resposta antiga simulada"},
    ]
    app = _real_app_with_fake_chat_api(monkeypatch, api, chat_history=simulated_history)
    question = _conversation_recovery_fixture()["messages"][0]["text"]
    app.text_area(key="message_input").set_value(question)
    app.button(key="send_message").click().run()

    conversation_id = app.session_state["conversation_id"]
    assert conversation_id.startswith("conv_") and 8 <= len(conversation_id.removeprefix("conv_")) <= 40
    assert api.posts == [
        {
            "message": question,
            "profile_id": "reference",
            "profile_version": 1,
            "router_mechanism": "llm",
            "conversation_id": conversation_id,
        }
    ]
    assert app.session_state["pending_message"] == {
        "conversation_id": conversation_id,
        "message": question,
        "baseline_message_count": 0,
    }
    assert app.session_state["latest_run"] is None
    assert not app.exception
    app.run()
    assert len(api.posts) == 1 and not api.gets
    app.selectbox(key="mode").select("Simulado").run()
    assert len(api.posts) == 1 and not api.gets
    assert not any(button.key == "recover_pending_conversation" for button in app.button)
    app.selectbox(key="mode").select("Real (API)").run()
    assert len(api.posts) == 1 and not api.gets

    app.button(key="recover_pending_conversation").click().run()
    assert len(api.posts) == 1 and api.gets == [conversation_id]
    assert app.session_state["pending_message"] is not None
    assert any("ainda não está disponível" in str(item.value).lower() for item in app.info)
    app.run()
    assert len(api.posts) == 1 and len(api.gets) == 1

    app.button(key="recover_pending_conversation").click().run()
    assert len(api.posts) == 1 and len(api.gets) == 2
    assert app.session_state["pending_message"] is not None
    assert any("ainda não há resposta nova" in str(item.value).lower() for item in app.info)

    app.button(key="recover_pending_conversation").click().run()
    assert len(api.posts) == 1 and len(api.gets) == 3
    assert app.session_state["pending_message"] is None
    assert app.session_state["latest_run"] is None
    assert app.session_state["recovered_without_request_id"] is True
    assert app.session_state["chat_history"] == simulated_history + [
        {"role": "user", "text": question},
        {"role": "assistant", "text": "Resposta sintética persistida no histórico."},
    ]
    assert any("Resposta sintética persistida no histórico." in str(item.value) for item in app.markdown)
    assert not app.exception
    app.run()
    assert len(api.posts) == 1 and len(api.gets) == 3


def test_recovery_does_not_present_old_answer_for_pending_question(monkeypatch):
    old_history = [
        {"role": "user", "text": "Pergunta antiga"},
        {"role": "assistant", "text": "Resposta antiga"},
    ]
    backend_old_history = [
        {"role": "merchant", "text": "Pergunta antiga", "route": "knowledge", "status": None, "created_at": "2026-10-27T11:59:00+00:00"},
        {"role": "assistant", "text": "Resposta antiga", "route": "knowledge", "status": "answered", "created_at": "2026-10-27T11:59:01+00:00"},
    ]
    recovery_body = _conversation_recovery_fixture()
    question = recovery_body["messages"][0]["text"]

    class FakeChatAPI:
        execution_mode = "real"

        def __init__(self):
            self.posts = []
            self.gets = []

        def profiles(self):
            return SimulatedAPI().profiles()

        def send_message(self, payload):
            self.posts.append(dict(payload))
            raise httpx.ReadTimeout("synthetic timeout")

        def conversation(self, conversation_id):
            self.gets.append(conversation_id)
            if len(self.gets) == 1:
                raise httpx.ReadTimeout("synthetic GET timeout")
            if len(self.gets) == 2:
                return {**recovery_body, "messages": backend_old_history}
            return {**recovery_body, "messages": backend_old_history + recovery_body["messages"]}

    api = FakeChatAPI()
    app = _real_app_with_fake_chat_api(
        monkeypatch,
        api,
        conversation_id="conv_existing12345",
        chat_history=old_history,
        backend_message_count=len(old_history),
    )
    app.text_area(key="message_input").set_value(question)
    app.button(key="send_message").click().run()
    assert len(api.posts) == 1
    assert api.posts[0]["conversation_id"] == "conv_existing12345"
    assert app.session_state["pending_message"]["baseline_message_count"] == len(old_history)

    app.button(key="recover_pending_conversation").click().run()
    assert len(api.posts) == 1 and len(api.gets) == 1
    assert app.session_state["pending_message"] is not None
    assert any("consulta falhou" in str(item.value).lower() for item in app.warning)

    app.button(key="recover_pending_conversation").click().run()
    assert len(api.posts) == 1 and len(api.gets) == 2
    assert app.session_state["pending_message"] is not None
    assert app.session_state["chat_history"] == old_history
    assert "Resposta antiga recuperada" not in " ".join(str(item.value) for item in app.markdown)
    assert any("ainda não há resposta nova" in str(item.value).lower() for item in app.info)

    app.button(key="recover_pending_conversation").click().run()
    assert len(api.posts) == 1 and len(api.gets) == 3
    assert app.session_state["pending_message"] is None
    assert app.session_state["chat_history"] == old_history + [
        {"role": "user", "text": question},
        {"role": "assistant", "text": "Resposta sintética persistida no histórico."},
    ]
    assert app.session_state["backend_message_count"] == len(old_history) + 2
    assert not app.exception


def test_conversation_recovery_uses_existing_get_route_only():
    observed = []
    recovery_body = _conversation_recovery_fixture()

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json=recovery_body)

    client = APIClient(
        "https://backend.invalid",
        token="synthetic-session",
        transport=httpx.MockTransport(handle),
    )
    assert client.conversation("conv_synthetic1234") == recovery_body
    assert [item["role"] for item in recovery_body["messages"]] == ["merchant", "assistant"]
    assert all(set(item) == {"role", "text", "route", "status", "created_at"} for item in recovery_body["messages"])
    assert len(observed) == 1
    assert observed[0].method == "GET"
    assert observed[0].url.path == "/v1/conversations/conv_synthetic1234"
    assert observed[0].content == b""
    assert observed[0].headers["Authorization"] == "Bearer synthetic-session"
    client.close()
