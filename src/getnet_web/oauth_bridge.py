"""Browser bridge: the initiating Streamlit session remains open while the issuer logs in."""
from pathlib import Path

import streamlit.components.v1 as components

_bridge = components.declare_component("getnet_oauth", path=str(Path(__file__).parent / "oauth_component"))


def login_popup(url: str, state: str):
    return _bridge(url=url, key=f"oauth_login_{state}", default=None)


def return_from_popup(params: dict[str, list[str]]):
    _bridge(callback=params, key="oauth_callback")
