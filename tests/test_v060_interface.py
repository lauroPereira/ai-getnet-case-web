"""v0.6.0 interface: three areas, catalog scenarios, Results with checks and on-demand evaluation, profile
comparison with two charts, recovery messages and HTTP timeouts. Network-free (fixtures and fake transports)."""

from __future__ import annotations

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from getnet_web import api as api_module
from getnet_web.api import APIClient, APIError
from getnet_web.simulated import SimulatedAPI

from .test_interface import ROOT


def _app(**state):
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


@pytest.fixture
def calls(monkeypatch):
    """Counts every SimulatedAPI operation that would be a POST (or a GET of a run) in the real client."""
    observed: list[str] = []
    for name in (
        "send_message",
        "compare",
        "estimate_evaluation",
        "evaluate",
        "estimate_comparison",
        "create_comparison",
        "comparison",
        "run",
    ):
        original = getattr(SimulatedAPI, name)

        def wrapper(self, *args, _original=original, _name=name, **kwargs):
            observed.append(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(SimulatedAPI, name, wrapper)
    return observed


def test_demo_sees_attendance_and_results_and_admin_also_sees_administration():
    app = _app()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Atendimento", "Resultados"]
    app.selectbox(key="sim_role").select("admin").run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Atendimento", "Resultados", "Administração"]


def test_catalog_scenario_is_sent_once_with_its_id_and_reruns_do_not_post(calls):
    app = _app()
    labels = app.selectbox(key="scenario_choice").options
    assert labels[0] == "Mensagem livre" and len(labels) == 4
    app.selectbox(key="scenario_choice").select(labels[1]).run()
    app.button(key="send_scenario").click().run()
    assert not app.exception
    assert calls.count("send_message") == 1
    run = app.session_state["latest_run"]
    assert run["scenario_id"] == "SC-RECEIVABLES" and run["execution_mode"] == "simulated"
    posts = calls.count("send_message")
    for _ in range(3):
        app.run()
    app.selectbox(key="results_run_choice").select("req_simulated_latest").run()
    assert calls.count("send_message") == posts
    assert any("Estado do caso" in str(item.value) for item in app.caption)


def test_results_show_checks_unevaluated_and_evaluation_needs_explicit_confirmation(calls):
    app = _app()
    app.text_area(key="message_input").set_value("pergunta sintética")
    app.button(key="send_message").click().run()
    assert not app.exception
    text = " ".join(str(item.value) for item in [*app.caption, *app.markdown])
    assert "Não avaliado." in text and "Origem: mensagem livre" in text
    assert "Trechos recuperados" in text
    assert any("simulada" in str(item.value) for item in app.markdown)

    app.button(key="eval_estimate").click().run()
    assert calls.count("estimate_evaluation") == 1
    assert app.button(key="eval_run").disabled
    app.run()
    assert calls.count("evaluate") == 0
    app.checkbox(key="eval_confirm").set_value(True).run()
    assert not app.button(key="eval_run").disabled
    app.button(key="eval_run").click().run()
    assert not app.exception
    assert calls.count("evaluate") == 1
    app.run()
    app.run()
    assert calls.count("evaluate") == 1 and calls.count("send_message") == 1
    assert not any(button.key == "eval_run" for button in app.button)  # estimate consumed: no repeated click
    rendered = " ".join(str(item.value) for item in app.markdown)
    assert "Responde à pergunta 4" in rendered and "separado do atendimento" in rendered


def test_comparison_estimate_confirm_run_refresh_and_bar_selection(calls):
    app = _app()
    assert app.multiselect(key="cmp_profiles").value and len(app.multiselect(key="cmp_profiles").value) == 2
    app.button(key="cmp_estimate").click().run()
    assert calls.count("estimate_comparison") == 1
    assert app.button(key="cmp_run").disabled
    app.checkbox(key="cmp_confirm").set_value(True).run()
    app.button(key="cmp_run").click().run()
    assert calls.count("create_comparison") == 1 and calls.count("comparison") == 0
    assert any("Atualizar comparação" in str(item.value) for item in app.info)
    app.button(key="cmp_refresh").click().run()
    assert not app.exception
    assert calls.count("comparison") == 1
    assert len(app.get("vega_lite_chart")) == 2
    tables = " ".join(frame.value.to_csv() for frame in app.dataframe)
    assert "não avaliado" in tables and "router_unavailable" in tables
    before = list(calls)
    choice = app.selectbox(key="cmp_bar_choice")
    choice.select(choice.options[1]).run()
    app.run()
    assert calls == before  # selecting a bar only re-reads recorded data
    app.button(key="cmp_open_runs").click().run()
    assert not app.exception
    assert calls[len(before) :] == ["run"]  # one GET per run of the bar, no POST
    assert app.session_state["results_run_choice"] == "req_fixture_1"
    assert calls.count("create_comparison") == 1 and calls.count("send_message") == 0


def test_changed_comparison_selection_invalidates_the_estimate(calls):
    app = _app()
    app.button(key="cmp_estimate").click().run()
    app.number_input(key="cmp_repetitions").set_value(2).run()
    assert app.session_state["comparison_estimate"] is None
    assert not any(button.key == "cmp_run" for button in app.button)
    assert calls.count("create_comparison") == 0


def test_backend_errors_explain_the_recovery_action(monkeypatch):
    def refuse(self, payload):
        raise APIError(429, "demo_message_limit_reached", "This demo session reached its message limit.")

    monkeypatch.setattr(SimulatedAPI, "send_message", refuse)
    app = _app()
    app.text_area(key="message_input").set_value("pergunta sintética")
    app.button(key="send_message").click().run()
    message = " ".join(str(item.value) for item in app.error)
    assert "Encerre a sessão" in message and "demo_message_limit_reached" in message
    assert "Traceback" not in message


def test_paid_operations_get_the_long_timeout_and_reads_stay_short():
    observed = {}

    def handle(request):
        observed[request.url.path] = request.extensions["timeout"]["read"]
        if request.url.path == "/v1/profiles":
            return httpx.Response(200, json={"profiles": []})
        if request.url.path == "/v1/scenarios":
            return httpx.Response(200, json={"scenarios": []})
        return httpx.Response(200, json={"message": {}, "run": {}})

    client = APIClient("https://backend.invalid", token="synthetic", transport=httpx.MockTransport(handle))
    client.profiles()
    client.scenarios()
    client.send_message({"message": "x"})
    client.evaluate("req_1", {"confirmed": True})
    client.estimate_evaluation("req_1")
    client.comparison("pcmp_1")
    client.close()
    assert api_module.LONG_TIMEOUT_SECONDS == 105.0 and api_module.DEFAULT_TIMEOUT_SECONDS == 20.0
    assert observed["/v1/messages"] == observed["/v1/runs/req_1/evaluations"] == 105.0
    assert observed["/v1/profiles"] == observed["/v1/scenarios"] == 20.0
    assert observed["/v1/runs/req_1/evaluations/estimate"] == observed["/v1/comparisons/pcmp_1"] == 20.0


def test_new_operations_use_the_documented_v1_routes():
    observed = []

    def handle(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"scenarios": [], "comparison_id": "pcmp_1"})

    client = APIClient("https://backend.invalid", token="synthetic", transport=httpx.MockTransport(handle))
    client.scenarios()
    client.estimate_evaluation("req_1")
    client.evaluate("req_1", {"confirmed": True})
    client.estimate_comparison({"profiles": []})
    client.create_comparison({"confirmed": True})
    client.comparison("pcmp_1")
    client.close()
    assert observed == [
        ("GET", "/v1/scenarios"),
        ("POST", "/v1/runs/req_1/evaluations/estimate"),
        ("POST", "/v1/runs/req_1/evaluations"),
        ("POST", "/v1/comparisons/estimate"),
        ("POST", "/v1/comparisons"),
        ("GET", "/v1/comparisons/pcmp_1"),
    ]


def test_unstructured_gateway_error_becomes_a_clean_api_error():
    client = APIClient(
        "https://backend.invalid",
        token="synthetic",
        transport=httpx.MockTransport(lambda request: httpx.Response(502, text="<html>bad gateway</html>")),
    )
    with pytest.raises(APIError) as error:
        client.profiles()
    assert error.value.status_code == 502 and error.value.code == "http_error"
    assert "html" not in str(error.value)
    client.close()


def test_shared_interface_can_lock_the_backend_address(monkeypatch):
    monkeypatch.setenv("LOCK_BACKEND_URL", "true")
    monkeypatch.setenv("BACKEND_API_URL", "http://127.0.0.1:8000")
    app = _app()
    app.selectbox(key="mode").select("Real (API)").run()
    field = next(item for item in app.text_input if item.label == "URL da API backend")
    assert field.disabled and field.value == "http://127.0.0.1:8000"


# --- review r1 corrections (task 60) -----------------------------------------------------------------------------


def _comparison_ready(app):
    app.button(key="cmp_estimate").click().run()
    app.checkbox(key="cmp_confirm").set_value(True).run()
    app.button(key="cmp_run").click().run()
    app.button(key="cmp_refresh").click().run()
    assert not app.exception
    return app


def test_failed_cells_and_chart_points_open_their_runs_by_get(calls):  # P3-07
    app = _comparison_ready(_app())
    before = len(calls)
    cell = app.selectbox(key="cmp_cell_choice")
    failed = next(option for option in cell.options if "req_fixture_4" in option)
    cell.select(failed).run()
    assert len(calls) == before
    app.button(key="cmp_open_cell").click().run()
    assert calls[before:] == ["run"] and app.session_state["results_run_choice"] == "req_fixture_4"
    point = app.selectbox(key="cmp_point_choice")
    point.select("hybrid v1").run()
    before = len(calls)
    app.button(key="cmp_open_point").click().run()
    assert not app.exception
    assert calls[before:] == ["run", "run"] and app.session_state["results_run_choice"] == "req_fixture_3"
    assert "create_comparison" in calls and calls.count("create_comparison") == 1


def test_unknown_cost_is_shown_as_unknown_and_not_plotted_as_zero(monkeypatch):  # P3-09c
    original = SimulatedAPI.comparison

    def unknown_cost(self, comparison_id):
        result = original(self, comparison_id)
        result["aggregates"]["profiles"][1]["cost_mean_usd"] = None
        result["cells"][2]["cost_usd"] = None
        return result

    monkeypatch.setattr(SimulatedAPI, "comparison", unknown_cost)
    app = _comparison_ready(_app())
    assert any("custo desconhecido" in str(item.value) for item in app.caption)
    assert app.selectbox(key="cmp_point_choice").options == ["reference v1"]
    tables = " ".join(frame.value.to_csv() for frame in app.dataframe)
    assert "desconhecido" in tables


def test_shared_interface_offers_only_the_demo_simulated_role(monkeypatch):  # P3-10
    monkeypatch.setenv("LOCK_BACKEND_URL", "true")
    app = _app()
    assert app.selectbox(key="sim_role").options == ["demo"]
    assert [tab.label for tab in app.tabs] == ["Atendimento", "Resultados"]


class _TimeoutAdminAPI:
    execution_mode = "real"

    def __init__(self):
        self.actions = {"build": 0, "test": 0, "activate": 0}

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
                "draft_revision": 1,
            }
        ]

    def kb_versions(self):
        candidate = {"version_id": "kb_candidate", "state": "ready", "test_result": {"passed": True}}
        return {"active_version_id": "kb_initial", "previous_version_id": None, "versions": [candidate]}

    def kb_document(self, document_id):
        return {
            "document_id": document_id,
            "source_kind": "simulated_internal",
            "content": "Published synthetic content",
            "content_hash": "a" * 64,
            "draft": None,
        }

    def kb_estimate(self, revisions):
        return {
            "digest": "synthetic-digest",
            "documents": 1,
            "chunks": 3,
            "embedding_model": "synthetic-embedder",
            "estimated_cost_usd": 0.0,
            "active_version_id": "kb_initial",
            "revisions": revisions,
        }

    def kb_build(self, payload):
        self.actions["build"] += 1
        raise httpx.ReadTimeout("synthetic build timeout")

    def kb_test(self, version_id, payload):
        self.actions["test"] += 1
        raise httpx.ReadTimeout("synthetic test timeout")

    def kb_activate(self, version_id, payload):
        self.actions["activate"] += 1
        raise httpx.ConnectError("synthetic connection failure")


def test_kb_transport_failures_never_crash_and_never_allow_a_blind_repeat(monkeypatch):  # P3-05
    api = _TimeoutAdminAPI()
    monkeypatch.setattr("getnet_web.api.get_session_client", lambda *_args, **_kwargs: api)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    app.session_state["api_token"] = "synthetic-session"
    app.session_state["api_role"] = "admin"
    app.run()
    app.selectbox(key="mode").select("Real (API)").run()
    assert not app.exception

    app.button(key="kb_estimate_action").click().run()
    app.checkbox(key="kb_confirm_build").set_value(True).run()
    app.button(key="kb_build_action").click().run()
    assert not app.exception
    assert api.actions["build"] == 1 and "kb_estimate" not in app.session_state
    assert any("recarregue a página" in str(item.value) for item in app.warning)
    assert not any(button.key == "kb_build_action" for button in app.button)  # nothing left to click again
    app.run()
    assert api.actions["build"] == 1

    app.checkbox(key="kb_confirm_test").set_value(True).run()
    app.button(key="kb_test_action").click().run()
    assert not app.exception and api.actions["test"] == 1
    assert app.checkbox(key="kb_confirm_test").value is False
    assert app.button(key="kb_test_action").disabled

    app.checkbox(key="kb_confirm_activate").set_value(True).run()
    app.button(key="kb_activate_action").click().run()
    assert not app.exception and api.actions["activate"] == 1
    assert app.checkbox(key="kb_confirm_activate").value is False


def test_unknown_costs_of_bars_and_running_evaluations_are_never_numbers(monkeypatch, calls):  # P3-R2-04
    original = SimulatedAPI.comparison

    def partial_cost(self, comparison_id):
        result = original(self, comparison_id)
        result["aggregates"]["cost_by_agent_model"][0]["cost_unknown"] = True
        return result

    monkeypatch.setattr(SimulatedAPI, "comparison", partial_cost)
    app = _comparison_ready(_app())
    options = app.selectbox(key="cmp_bar_choice").options
    assert options[0].endswith("custo desconhecido") and "US$" not in options[0]
    assert any("fora do gráfico (custo desconhecido)" in str(item.value) for item in app.caption)

    running = {
        "evaluation_id": "eval_running",
        "state": "running",
        "rubric": "judge",
        "rubric_version": "2",
        "evaluator": {"provider": "fixture", "model": "fixture-judge", "parameters": {}},
        "cost_usd": None,
        "critical_failures": [],
    }
    original_run = SimulatedAPI.run
    monkeypatch.setattr(SimulatedAPI, "run", lambda self, rid: original_run(self, rid) | {"evaluations": [running]})
    app = _app()
    app.text_area(key="message_input").set_value("pergunta sintética")
    app.button(key="send_message").click().run()
    text = " ".join(str(item.value) for item in app.markdown)
    assert "custo da avaliação desconhecido" in text and "US$ 0.000000 (separado" not in text
    assert any("em andamento" in str(item.value) for item in app.caption)


# --- QA r1 corrections (task 61) ----------------------------------------------------------------------------------


def test_reais_are_shown_literally_and_not_as_a_formula():  # P3-QA-02
    app = _app()
    app.text_area(key="message_input").set_value("O Pix de R$ 80,00 e o débito de R$ 148,06")
    app.button(key="send_message").click().run()
    assert not app.exception
    rendered = [str(item.value) for item in app.markdown if "R" in str(item.value) and "80,00" in str(item.value)]
    assert rendered and all(r"R\$ 80,00" in item and r"R\$ 148,06" in item for item in rendered)
    stored = app.session_state["chat_history"][-1]["text"]
    assert "R$ 80,00" in stored and r"\$" not in stored  # what was recorded is unchanged


def test_case_state_after_recovery_shows_the_recovered_reply(monkeypatch):  # P3-QA-03
    from .test_interface import _conversation_recovery_fixture

    body = _conversation_recovery_fixture()
    question = body["messages"][0]["text"]

    class FakeChatAPI:
        execution_mode = "real"

        def profiles(self):
            return SimulatedAPI().profiles()

        def send_message(self, payload):
            raise httpx.ReadTimeout("synthetic timeout")

        def conversation(self, conversation_id):
            return body

    monkeypatch.setattr("getnet_web.api.get_session_client", lambda *_args, **_kwargs: FakeChatAPI())
    app = _app(
        api_token="synthetic-session",
        api_role="demo",
        case_view={"status": "resolved", "route": "support", "state": "resolved", "protocol": None},
    )
    app.selectbox(key="mode").select("Real (API)").run()
    app.text_area(key="message_input").set_value(question)
    app.button(key="send_message").click().run()
    app.button(key="recover_pending_conversation").click().run()
    assert not app.exception
    view = app.session_state["case_view"]
    assert (view["route"], view["status"], view["state"]) == ("knowledge", "answered", "answered")
    assert any("rota knowledge" in str(item.value) and "status answered" in str(item.value) for item in app.caption)


def test_profile_selector_starts_on_reference_with_portuguese_names(monkeypatch):  # O-1
    english = [
        {"profile_id": "adjusted", "name": "Adjusted", "version": 1, "state": "published", "agents": {}},
        {"profile_id": "hybrid", "name": "Hybrid", "version": 1, "state": "published", "agents": {}},
        {"profile_id": "reference", "name": "Reference", "version": 1, "state": "published", "agents": {}},
    ]
    monkeypatch.setattr(SimulatedAPI, "profiles", lambda self: english)
    app = _app()
    selector = app.selectbox(key="profile")
    assert selector.options == ["Referência · v1", "Híbrido · v1", "Ajustado · v1"]
    assert selector.value == "Referência · v1"
    assert app.multiselect(key="cmp_profiles").value == ["Referência · v1", "Híbrido · v1"]


def test_cost_quality_chart_has_no_overlapping_text_labels(calls):  # O-4
    import json as json_module

    app = _comparison_ready(_app())
    spec = json_module.loads(app.get("vega_lite_chart")[0].proto.spec)
    assert "layer" not in spec and spec["mark"]["type"] == "circle"  # no text marks drawn over the points
    assert spec["encoding"]["color"]["title"] == "perfil · amostra · duração média"


def test_candidate_test_cap_is_prefilled_with_the_build_estimate(monkeypatch):  # O-2
    class Admin(_TimeoutAdminAPI):
        def kb_versions(self):
            state = super().kb_versions()
            state["versions"][0]["manifest"] = {"estimated_cost_usd": 0.0012}
            return state

    monkeypatch.setattr("getnet_web.api.get_session_client", lambda *_args, **_kwargs: Admin())
    app = _app(api_token="synthetic-session", api_role="admin")
    app.selectbox(key="mode").select("Real (API)").run()
    assert not app.exception
    assert app.number_input(key="kb_test_max_cost").value == pytest.approx(0.0012)



# --- review r4 residue (task 61, round 5) -------------------------------------------------------------------------

TWO_AMOUNTS = "Recebi R$ 80,00 e paguei R$ 148,06"


def _escaped(text):
    return r"R\$ 80,00" in text and r"R\$ 148,06" in text


def test_pending_question_is_shown_literally(monkeypatch):  # P3-R4-01
    class FakeChatAPI:
        execution_mode = "real"

        def profiles(self):
            return SimulatedAPI().profiles()

        def send_message(self, payload):
            raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr("getnet_web.api.get_session_client", lambda *_args, **_kwargs: FakeChatAPI())
    app = _app(api_token="synthetic-session", api_role="demo")
    app.selectbox(key="mode").select("Real (API)").run()
    app.text_area(key="message_input").set_value(TWO_AMOUNTS)
    app.button(key="send_message").click().run()
    pending = [str(item.value) for item in app.markdown if str(item.value).startswith("Pergunta pendente")]
    assert pending and _escaped(pending[0])
    assert app.session_state["pending_message"]["message"] == TWO_AMOUNTS  # what is sent and stored is unchanged


def test_evaluator_comment_and_backend_errors_are_shown_literally(monkeypatch):  # P3-R4-01
    original = SimulatedAPI.evaluate

    def with_amounts(self, request_id, payload):
        result = original(self, request_id, payload)
        result["result"] = dict(result["result"], comment=TWO_AMOUNTS)
        return result

    monkeypatch.setattr(SimulatedAPI, "evaluate", with_amounts)
    app = _app()
    app.text_area(key="message_input").set_value("pergunta sintética")
    app.button(key="send_message").click().run()
    app.button(key="eval_estimate").click().run()
    app.checkbox(key="eval_confirm").set_value(True).run()
    app.button(key="eval_run").click().run()
    assert not app.exception
    comment = [str(item.value) for item in app.markdown if "Responde à pergunta" in str(item.value)]
    assert comment and _escaped(comment[0])

    def refuse(self, payload):
        raise APIError(422, "invalid_request", TWO_AMOUNTS)

    monkeypatch.setattr(SimulatedAPI, "send_message", refuse)
    app.text_area(key="message_input").set_value("outra pergunta")
    app.button(key="send_message").click().run()
    assert any(_escaped(str(item.value)) for item in app.error)
