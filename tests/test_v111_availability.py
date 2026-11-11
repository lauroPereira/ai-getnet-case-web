"""v1.1.0 (backend task 111, D3, CA-78): the interface marks the profiles and the JEV Router that this backend cannot
run, says which key the backend is missing and does not send them; with the keys everything is as before, and a
backend without the availability fields keeps the current behavior. Network-free: a fake assistant that answers
`GET /v1/profiles` as the backend does, and httpx.MockTransport for the HTTP client."""

from __future__ import annotations

import json

import httpx

from getnet_web.api import APIClient
from getnet_web.generated_api import OPERATIONS
from getnet_web.simulated import SimulatedAPI

from .test_interface import ROOT
from .test_v070_interface import (  # noqa: F401 (wire is a fixture)
    FakeTicketing,
    ScriptedChat,
    login,
    real_app,
    send,
    texts,
    wire,
)

DEEPSEEK = ["deepseek"]
NO_KEYS = {"reference": [], "hybrid": DEEPSEEK, "adjusted": DEEPSEEK}
EVERY_KEY = {"reference": [], "hybrid": [], "adjusted": []}
DEEPSEEK_MISSING = "Perfil Híbrido indisponível: falta `DEEPSEEK_API_KEY` no backend."
JEV_MISSING = "JEV indisponível: falta `TYPESAFE_API_KEY` no backend."


class AvailabilityChat(ScriptedChat):
    """The assistant with the availability of a v1.1.0 backend: `missing` maps a profile to its providers without a
    key; `jev` is `router_mechanisms.jev`. `legacy` answers as a backend before v1.1.0 (no availability field)."""

    def __init__(self, *args, missing=None, jev=False, legacy=False, **kwargs):
        super().__init__("merchant", "merchant:cliente1988", *args, **kwargs)
        self.missing, self.jev, self.legacy = dict(missing or NO_KEYS), jev, legacy
        self.compares: list[dict] = []
        self.estimates: list[dict] = []

    def profile_listing(self):
        profiles = [dict(item) for item in SimulatedAPI().profiles()]
        if self.legacy:
            return {"profiles": profiles}
        for item in profiles:
            missing = self.missing[item["profile_id"]]
            item |= {"available": not missing, "missing_providers": missing}
        return {"profiles": profiles, "router_mechanisms": {"llm": True, "jev": self.jev}}

    def scenarios(self):
        return SimulatedAPI().scenarios()

    def compare(self, payload):
        self.compares.append(payload)
        return SimulatedAPI().compare(payload)

    def estimate_comparison(self, payload):
        self.estimates.append(payload)
        return SimulatedAPI().estimate_comparison(payload)


def merchant_app(wire, chat):  # noqa: F811 (the fixture's value)
    wire(chat, FakeTicketing())
    app = login(real_app(), "cliente1988")
    assert not app.exception
    return app


def options(widget):
    return list(widget.options)


def test_without_the_keys_the_jev_and_the_deepseek_profiles_cannot_be_sent(wire):  # noqa: F811
    chat = AvailabilityChat(outcomes=["fixture"])
    app = merchant_app(wire, chat)

    profile = app.selectbox(key="profile")
    assert profile.value == "Referência · v1"  # the default is an available profile
    assert options(profile) == [
        "Referência · v1", "Híbrido · v1 · indisponível neste backend", "Ajustado · v1 · indisponível neste backend"
    ]
    router = app.radio(key="router_choice_reference")
    assert router.value == "llm" and router.disabled
    assert JEV_MISSING in texts(app)

    profile.select("Híbrido · v1").run()
    assert not app.exception
    assert DEEPSEEK_MISSING in [str(item.value) for item in app.warning]
    hybrid_router = app.radio(key="router_choice_hybrid")
    assert hybrid_router.value == "llm" and hybrid_router.disabled  # Hybrid does not pull the missing JEV
    assert app.button(key="send_message").disabled  # a browser cannot click it (AppTest refuses too)
    app.selectbox(key="scenario_choice").select_index(1).run()
    assert app.button(key="send_scenario").disabled
    assert chat.payloads == []  # nothing was sent

    app.selectbox(key="profile").select("Referência · v1").run()
    app.selectbox(key="scenario_choice").select("Mensagem livre").run()
    send(app, "Quando recebo minhas vendas?")
    assert [(p["profile_id"], p["router_mechanism"]) for p in chat.payloads] == [("reference", "llm")]


def test_without_the_jev_the_router_only_comparison_is_disabled_with_the_key(wire):  # noqa: F811
    chat = AvailabilityChat(missing=EVERY_KEY, jev=False)
    app = merchant_app(wire, chat)
    assert app.radio(key="comparison_selection").disabled and app.radio(key="comparison_selection").value == "llm"
    assert app.button(key="compare_routers").disabled
    assert f"{JEV_MISSING} A comparação só dos Routers precisa dele." in texts(app)
    assert chat.compares == []


def test_an_unavailable_profile_cannot_start_a_router_only_comparison(wire):  # noqa: F811
    chat = AvailabilityChat(missing=NO_KEYS, jev=True)
    app = merchant_app(wire, chat)
    assert not app.button(key="compare_routers").disabled
    app.selectbox(key="profile").select("Híbrido · v1").run()
    assert app.button(key="compare_routers").disabled
    assert DEEPSEEK_MISSING in texts(app)
    assert chat.compares == []


def test_the_profile_comparison_offers_only_what_this_backend_runs(wire):  # noqa: F811
    chat = AvailabilityChat()
    app = merchant_app(wire, chat)
    chosen = app.multiselect(key="cmp_profiles")
    assert chosen.value == ["Referência · v1"]  # the default leaves the unavailable profiles out
    assert "Híbrido · v1 · indisponível neste backend" in options(chosen)
    assert "A comparação precisa de dois perfis disponíveis neste backend." in texts(app)
    mechanism = app.radio(key="cmp_mechanism")
    assert mechanism.value == "llm" and mechanism.disabled
    assert texts(app).count(JEV_MISSING) >= 2  # attendance and profile comparison

    chosen.select("Híbrido · v1").run()
    assert not app.exception
    assert DEEPSEEK_MISSING in [str(item.value) for item in app.warning]
    assert app.button(key="cmp_estimate").disabled
    assert chat.estimates == []


def test_with_every_key_everything_is_as_before(wire):  # noqa: F811
    chat = AvailabilityChat(missing=EVERY_KEY, jev=True, outcomes=["fixture"])
    app = merchant_app(wire, chat)
    assert options(app.selectbox(key="profile")) == ["Referência · v1", "Híbrido · v1", "Ajustado · v1"]
    assert not app.radio(key="router_choice_reference").disabled
    assert app.multiselect(key="cmp_profiles").value == ["Referência · v1", "Híbrido · v1"]
    assert not app.radio(key="cmp_mechanism").disabled and not app.button(key="cmp_estimate").disabled
    assert not app.button(key="compare_routers").disabled
    assert "indisponível" not in texts(app) and "API_KEY" not in texts(app)
    app.selectbox(key="profile").select("Híbrido · v1").run()
    assert app.radio(key="router_choice_hybrid").value == "jev"  # Hybrid still defaults to the JEV
    send(app, "Quando recebo minhas vendas?")
    assert [(p["profile_id"], p["router_mechanism"]) for p in chat.payloads] == [("hybrid", "jev")]


def test_a_jev_only_missing_key_keeps_the_profiles_and_moves_hybrid_to_the_llm(wire):  # noqa: F811
    chat = AvailabilityChat(missing=EVERY_KEY, jev=False, outcomes=["fixture"])
    app = merchant_app(wire, chat)
    assert "indisponível neste backend" not in " ".join(options(app.selectbox(key="profile")))
    app.selectbox(key="profile").select("Híbrido · v1").run()
    assert app.radio(key="router_choice_hybrid").value == "llm"
    send(app, "Quando recebo minhas vendas?")
    assert [(p["profile_id"], p["router_mechanism"]) for p in chat.payloads] == [("hybrid", "llm")]


def test_a_jev_choice_made_before_the_key_went_away_goes_back_to_the_llm(wire):  # noqa: F811
    chat = AvailabilityChat(missing=EVERY_KEY, jev=True, outcomes=["fixture"])
    app = merchant_app(wire, chat)
    app.radio(key="router_choice_reference").set_value("jev").run()
    app.radio(key="cmp_mechanism").set_value("jev").run()
    chat.jev = False  # the backend restarted without TYPESAFE_API_KEY
    app.run()
    assert not app.exception
    assert app.radio(key="router_choice_reference").value == "llm"
    assert app.radio(key="cmp_mechanism").value == "llm"
    send(app, "Quando recebo minhas vendas?")
    assert chat.payloads[-1]["router_mechanism"] == "llm"


def test_a_backend_without_the_fields_keeps_the_current_behavior(wire):  # noqa: F811
    chat = AvailabilityChat(legacy=True, outcomes=["fixture"])
    app = merchant_app(wire, chat)
    assert options(app.selectbox(key="profile")) == ["Referência · v1", "Híbrido · v1", "Ajustado · v1"]
    assert not app.radio(key="router_choice_reference").disabled
    assert app.multiselect(key="cmp_profiles").value == ["Referência · v1", "Híbrido · v1"]
    assert not app.button(key="compare_routers").disabled
    assert "indisponível" not in texts(app)
    app.selectbox(key="profile").select("Híbrido · v1").run()
    assert app.radio(key="router_choice_hybrid").value == "jev"
    send(app, "Quando recebo minhas vendas?")
    assert [(p["profile_id"], p["router_mechanism"]) for p in chat.payloads] == [("hybrid", "jev")]


def test_simulated_mode_keeps_every_option():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not app.exception
    assert options(app.selectbox(key="profile")) == ["Referência · v1", "Híbrido · v1", "Ajustado · v1"]
    assert not app.radio(key="router_choice_reference").disabled
    assert "indisponível" not in texts(app)


def test_the_client_reads_the_whole_profile_listing():
    body = {
        "profiles": [{"profile_id": "hybrid", "available": False, "missing_providers": ["deepseek"]}],
        "router_mechanisms": {"llm": True, "jev": False},
    }
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("Authorization")))
        return httpx.Response(200, json=body)

    client = APIClient("http://backend", token="synthetic-token", transport=httpx.MockTransport(handler))
    try:
        assert client.profile_listing() == body
        assert client.profiles() == body["profiles"]
    finally:
        client.close()
    assert seen == [("GET", "/v1/profiles", "Bearer synthetic-token")] * 2


def test_the_snapshot_documents_the_availability_fields():
    schema = json.loads((ROOT / "openapi-v1.json").read_text(encoding="utf-8"))
    operation = schema["paths"]["/v1/profiles"]["get"]
    example = operation["responses"]["200"]["content"]["application/json"]["example"]
    assert example["router_mechanisms"] == {"llm": True, "jev": False}
    assert {"available", "missing_providers"} <= set(example["profiles"][0])
    assert OPERATIONS["list_profiles"] == ("GET", "/v1/profiles") and len(OPERATIONS) == 29
