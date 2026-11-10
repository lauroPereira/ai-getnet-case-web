"""The login component's script (src/getnet_web/oauth_component/index.html) run by Node with a minimal DOM: Streamlit
may render a component several times, so the redirect page must publish once and the waiting tab must hand the
server at most one value per attempt, and close the popup it opened. Skipped without Node."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from .test_interface import ROOT

NODE = shutil.which("node")
HARNESS = r"""
const posted = [], channels = [], opened = [], listeners = [];
let closes = 0;
class BroadcastChannel {
  constructor(name) { this.name = name; this.sent = []; this.onmessage = null; this.closed = false; channels.push(this); }
  postMessage(m) { if (this.closed) throw new Error("closed channel"); this.sent.push(m); }
  close() { this.closed = true; }
}
const parentWindow = {
  postMessage: m => posted.push(m),
  history: { replaceState: () => {} },
  location: { pathname: "/" },
};
const elements = { login: { hidden: true, onclick: null }, notice: { textContent: "" } };
globalThis.BroadcastChannel = BroadcastChannel;
globalThis.document = { getElementById: id => elements[id] };
globalThis.window = {
  parent: parentWindow,
  addEventListener: (type, f) => listeners.push(f),
  open: url => { opened.push(url); return { closed: false, close() { closes += 1; this.closed = true; } }; },
};
const render = args => listeners.forEach(f => f({ source: parentWindow, data: { type: "streamlit:render", args } }));
const values = () => posted.filter(m => m.type === "streamlit:setComponentValue").map(m => m.value);
eval(SCRIPT);
const out = {};
const scenario = process.argv[2];
if (scenario === "popup") {
  const callback = { code: ["c"], state: ["S1"], iss: ["http://issuer"] };
  for (let i = 0; i < 3; i++) render({ callback });
  out.published = channels[0].sent.length;
  out.closed = channels[0].closed;
} else {
  render({ url: "http://issuer/v1/auth/authorize?state=S1", expires_in_ms: 300000 });
  render({ url: "http://issuer/v1/auth/authorize?state=S1", expires_in_ms: 299000 });
  elements.login.onclick();
  const deliver = data => channels[0].onmessage({ data });
  deliver({ code: ["c"], state: ["OTHER"], iss: ["http://issuer"] });
  deliver({ code: ["c"], state: ["S1"], iss: ["http://issuer"] });
  deliver({ code: ["c"], state: ["S1"], iss: ["http://issuer"] });
  out.first = values().length;
  out.closes = closes;
  out.opened = opened.length;
  render({ url: "http://issuer/v1/auth/authorize?state=S2", expires_in_ms: 300000 });
  deliver({ code: ["d"], state: ["S2"], iss: ["http://issuer"] });
  out.values = values().map(v => v.state[0]);
}
console.log(JSON.stringify(out));
"""


def run(scenario, tmp_path):
    page = (ROOT / "src" / "getnet_web" / "oauth_component" / "index.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", page, re.DOTALL).group(1)
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS.replace("eval(SCRIPT)", f"eval({json.dumps(script)})"), encoding="utf-8")
    done = subprocess.run([NODE, str(harness), scenario], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_redirect_page_publishes_once(tmp_path):
    assert run("popup", tmp_path) == {"published": 1, "closed": True}


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_tab_hands_one_value_per_attempt_and_closes_the_popup(tmp_path):
    result = run("tab", tmp_path)
    assert result == {"first": 1, "closes": 1, "opened": 1, "values": ["S1", "S2"]}
