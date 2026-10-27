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


def test_generated_client_operations_match_openapi_v1_contract():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    operations = {
        details["operationId"]: (method.upper(), path)
        for path, path_item in schema["paths"].items()
        if path.startswith("/v1/")
        for method, details in path_item.items()
        if method in {"get", "post", "put", "delete"}
    }
    assert len(OPERATIONS) == 11
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
