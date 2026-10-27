"""Network-free fixtures implementing the same interface as the HTTP client."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from getnet_web.api import APIError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class SimulatedAPI:
    execution_mode = "simulated"

    def __init__(self, *, recorded: bool = False, used: set[str] | None = None) -> None:
        self.recorded = recorded
        self.execution_mode = "recorded" if recorded else "simulated"
        self._used = used if used is not None else set()
        self._fixtures = json.loads(
            (FIXTURES / ("recorded.json" if recorded else "simulated.json")).read_text(encoding="utf-8")
        )
        self._model_catalog = json.loads((FIXTURES / "model_catalog.json").read_text(encoding="utf-8"))

    def login(self, access_key: str) -> dict:
        raise APIError(400, "not_required", "Simulated sessions do not require an access key.")

    def logout(self) -> None:
        return None

    def profiles(self) -> list[dict]:
        return self._fixtures["profiles"]

    def model_catalog(self) -> dict:
        return self._model_catalog

    def compare(self, payload: dict) -> dict:
        result = dict(self._fixtures["comparison"])
        result["comparison_id"] = f"cmp_simulated_{uuid.uuid4().hex}"
        result["selected_mechanism"] = payload["selected_mechanism"]
        result["execution_mode"] = "recorded" if self.recorded else "simulated"
        return result

    def send_message(self, payload: dict) -> dict:
        comparison_id = payload.get("comparison_id")
        if comparison_id:
            if comparison_id in self._used:
                raise APIError(409, "comparison_conflict", "Comparison has already been used.")
            self._used.add(comparison_id)
        result = dict(self._fixtures["message"])
        message = dict(result["message"])
        run = dict(result["run"])
        message["answer"] = f"[Modo {self.execution_mode}] Recebi sua mensagem: {payload['message']}"
        message["request_id"] = "req_simulated_latest"
        message["conversation_id"] = payload.get("conversation_id") or "conv_simulated00000001"
        run["profile_id"] = payload["profile_id"]
        run["profile_version"] = payload["profile_version"]
        run["router_mechanism"] = payload["router_mechanism"]
        run["execution_mode"] = "recorded" if self.recorded else "simulated"
        run["comparison_id"] = comparison_id
        return {"message": message, "run": run}

    def run(self, request_id: str) -> dict:
        if request_id != "req_simulated_latest":
            raise APIError(404, "not_found", "Run not found.")
        run = dict(self._fixtures["message"]["run"])
        run["execution_mode"] = self.execution_mode
        return run

    def conversation(self, conversation_id: str) -> dict:
        result = dict(self._fixtures["conversation"])
        result["conversation_id"] = conversation_id
        return result

    def create_profile(self, payload: dict) -> dict:
        result = dict(self._fixtures["draft"])
        result["name"] = payload["name"]
        return result

    def update_profile(self, profile_id: str, payload: dict) -> dict:
        result = dict(self._fixtures["draft"])
        result["profile_id"] = profile_id
        result["version"] = payload["expected_version"] + 1
        result["agents"] = payload["agents"]
        return result

    def publish_profile(self, profile_id: str, expected_version: int) -> dict:
        result = dict(self._fixtures["published"])
        result["profile_id"] = profile_id
        result["version"] = expected_version
        return result
