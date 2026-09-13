"""HTTP surface tests - the endpoints a reviewer will actually poke."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.agent import LlmPlanner
from core.config import AgentConfig, AppConfig, NacConfig
from core.server import create_app
from idea import SPEC


@pytest.fixture
def client(tmp_path):
    config = AppConfig.from_env()
    config.nac = NacConfig(mode="simulator")
    config.agent = AgentConfig(provider="policy")
    config.db_path = str(tmp_path / "ledger.db")
    with TestClient(create_app(SPEC, config)) as test_client:
        yield test_client


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert SPEC.name in response.text
    # The spec payload must be inlined or the page is blank on load.
    assert "window.__SPEC__" in response.text
    assert "Planner fallback:" in response.text


def test_health_reports_mode_and_planner(client):
    body = client.get("/api/health").json()
    assert body["product"] == SPEC.name
    assert body["network"]["effective_source"] == "simulator"
    assert body["agent"]["configured_planner"] == "policy"
    assert body["agent"]["last_decision_planner"] is None
    assert body["agent"]["model_verified"] is False
    assert body["camara_apis"], "health must state which CAMARA APIs are wired"


def test_spec_endpoint_lists_scenarios_and_tools(client):
    body = client.get("/api/spec").json()
    assert len(body["scenarios"]) == len(SPEC.scenarios)
    assert len(body["tool_catalogue"]) == len(SPEC.policy.tool_names)
    for tool in body["tool_catalogue"]:
        assert tool["cost_units"] >= 0
        assert tool["reveals"] in {"boolean", "enum", "area", "mutates"}


def test_run_a_scenario(client):
    scenario = SPEC.scenarios[0]
    body = client.post("/api/run", json={"scenario_id": scenario.id}).json()
    assert body["level"] == scenario.expect_level
    assert body["met_expectation"] is True
    assert body["case_id"]
    assert body["planner"] == "policy"
    assert body["planner_error"] == ""

    health = client.get("/api/health").json()["agent"]
    assert health["configured_planner"] == "policy"
    assert health["last_decision_planner"] == "policy"
    assert health["last_decision_case_id"] == body["case_id"]
    assert health["model_verified"] is False


def test_health_never_presents_a_model_fallback_as_gemini(tmp_path, monkeypatch):
    """Configured and effective planners must remain auditable on the HTTP API."""

    def unavailable(_self, _case):
        raise RuntimeError("deliberately unavailable provider")

    monkeypatch.setattr(LlmPlanner, "_runner_for", unavailable)
    config = AppConfig.from_env()
    config.nac = NacConfig(mode="simulator")
    config.agent = AgentConfig(provider="gemini", api_key="test-key")
    config.db_path = str(tmp_path / "fallback-ledger.db")

    with TestClient(create_app(SPEC, config)) as test_client:
        before = test_client.get("/api/health").json()["agent"]
        assert before["configured_planner"] == "gemini"
        assert before["last_decision_planner"] is None

        decision = test_client.post(
            "/api/run", json={"scenario_id": SPEC.scenarios[0].id}
        ).json()
        assert decision["planner"] == "policy-fallback"
        assert "deliberately unavailable provider" in decision["planner_error"]

        after = test_client.get("/api/health").json()["agent"]
        assert after["configured_planner"] == "gemini"
        assert after["last_decision_planner"] == "policy-fallback"
        assert "deliberately unavailable provider" in after["last_decision_model_error"]
        assert after["model_verified"] is False


def test_unknown_scenario_is_a_404(client):
    assert client.post("/api/run", json={"scenario_id": "no-such-thing"}).status_code == 404


def test_run_all_returns_a_cost_comparison(client):
    body = client.post("/api/run-all", json={}).json()
    assert body["scenarios_run"] == len(SPEC.scenarios)
    assert body["expectations_met"] == len(SPEC.scenarios)
    assert body["total_cost_units"] < body["cost_if_everything_called"]


def test_ad_hoc_case_on_an_unknown_number(client):
    body = client.post("/api/case", json={"subject": "+905550001234"}).json()
    assert body["level"] in SPEC.policy.levels
    assert body["subject"] == "+905550001234"


def test_decisions_are_persisted_and_retrievable(client):
    run = client.post("/api/run", json={"scenario_id": SPEC.scenarios[0].id}).json()
    listing = client.get("/api/decisions").json()["decisions"]
    assert any(d["case_id"] == run["case_id"] for d in listing)

    single = client.get("/api/decisions/" + run["case_id"]).json()
    assert single["level"] == run["level"]
    assert client.get("/api/decisions/nope").status_code == 404


def test_stats_quantify_the_restraint(client):
    client.post("/api/run-all", json={})
    body = client.get("/api/stats").json()
    assert body["decisions"] == len(SPEC.scenarios)
    assert body["api_calls"] >= 0
    assert body["avg_cost_per_decision"] < body["cost_if_everything_called"]


def test_revoking_consent_stops_the_agent_making_calls(client):
    subject = SPEC.all_lines()[0].msisdn
    revoked = client.post("/api/consent/revoke", json={"subject": subject}).json()
    assert revoked["grants_withdrawn"] >= 1

    body = client.post("/api/case", json={"subject": subject}).json()
    assert body["evidence"] == [], "a withdrawal that still allows calls is not a withdrawal"
    assert body["skipped"]

    client.post("/api/consent/grant", json={"subject": subject})
    restored = client.post("/api/case", json={"subject": subject}).json()
    assert len(restored["evidence"]) >= 1


def test_consent_snapshot_shows_scopes(client):
    body = client.get("/api/consent").json()
    assert body["subjects"] >= len(SPEC.all_lines())
    assert body["active_grants"] >= 1


def test_simulator_can_be_changed_mid_demo(client):
    subject = SPEC.all_lines()[0].msisdn
    body = client.post(
        "/api/simulator/update",
        json={"subject": subject, "changes": {"reachability": "NOT_CONNECTED"}},
    ).json()
    assert body["applied"]["reachability"] == "NOT_CONNECTED"

    lines = client.get("/api/simulator").json()["lines"]
    changed = [line for line in lines if line["msisdn"] == subject][0]
    assert changed["reachability"] == "NOT_CONNECTED"


def test_reset_clears_the_ledger(client):
    client.post("/api/run", json={"scenario_id": SPEC.scenarios[0].id})
    assert client.get("/api/stats").json()["decisions"] >= 1
    client.post("/api/reset", json={})
    assert client.get("/api/stats").json()["decisions"] == 0
    agent = client.get("/api/health").json()["agent"]
    assert agent["last_decision_planner"] is None
    assert agent["last_decision_model_error"] is None


def test_websocket_replays_then_streams(client):
    client.post("/api/run", json={"scenario_id": SPEC.scenarios[0].id})
    with client.websocket_connect("/ws") as socket:
        event = socket.receive_json()
        assert event["topic"].startswith("case.")
        assert "seq" in event and "at" in event


def test_openapi_document_is_generated(client):
    body = client.get("/openapi.json").json()
    assert "/api/run" in body["paths"]
    assert "/api/health" in body["paths"]
