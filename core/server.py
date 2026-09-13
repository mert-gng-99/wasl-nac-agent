"""FastAPI application factory.

``create_app(spec)`` turns an :class:`~core.idea.IdeaSpec` into a running
prototype: a dashboard, a REST surface, and a WebSocket that streams the
agent's steps as they happen.

The endpoints a reviewer will actually use:

    GET  /                      the operator dashboard
    GET  /api/health            mode, credentials, planner in use
    GET  /api/spec              product metadata, tool catalogue, scenarios
    POST /api/run               run one scenario
    POST /api/run-all           run every scenario back to back
    POST /api/case              run an ad-hoc case against any number
    POST /api/consent/revoke    withdraw consent and watch calls get refused
    POST /api/simulator/update  change a line mid-demo
    WS   /ws                    live agent events

``/api/consent/revoke`` is there on purpose. The quickest way to show that the
consent gate is real is to turn it off and watch the agent get refused.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .agent import Agent, Case, Decision
from .camara import CamaraClient
from .config import AppConfig
from .consent import ConsentLedger
from .events import EventBus
from .idea import IdeaSpec
from .ledger import DecisionLedger
from .simulator import NetworkSimulator
from .tools import ToolRegistry
from .webui import render_dashboard

__all__ = ["create_app", "build_platform", "Platform"]


class RunScenario(BaseModel):
    scenario_id: str = Field(..., description="Scenario id from /api/spec")


class AdHocCase(BaseModel):
    subject: str = Field(..., description="MSISDN in E.164 form, for example +905551112233")
    # Everything below is optional: anything omitted is taken from the
    # product's own template case, so a bare phone number is a valid request.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_m: Optional[int] = None
    label: str = ""
    facts: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)


class SubjectRef(BaseModel):
    subject: str


class SimulatorUpdate(BaseModel):
    subject: str
    changes: Dict[str, Any] = Field(
        default_factory=dict,
        description="LineProfile fields to change, e.g. {\"reachability\": \"NOT_CONNECTED\"}",
    )


class Platform:
    """Everything one prototype needs, wired together."""

    def __init__(self, spec: IdeaSpec, config: Optional[AppConfig] = None) -> None:
        self.spec = spec
        self.config = config or AppConfig.from_env()
        self.events = EventBus()
        self.consent = ConsentLedger()
        self.simulator = NetworkSimulator(spec.all_lines())
        self.client = CamaraClient(
            self.config.nac, simulator=self.simulator, consent=self.consent
        )
        self.ledger = DecisionLedger(self.config.db_path)
        self.registry = ToolRegistry(list(spec.policy.tool_names))
        # Configuration says which planner the next case will attempt to use;
        # it is not evidence that a remote model call actually succeeded.  The
        # last completed decision makes that distinction visible to the API and
        # demo without retaining any prompt, key or raw provider response.
        self._last_planner_run: Dict[str, Any] = {
            "case_id": None,
            "planner": None,
            "planner_error": "",
            "created_at": None,
        }
        self._grant_enrolment_consent()

    def _grant_enrolment_consent(self) -> None:
        """Enrol every demo line under this product's consent plan.

        In production this happens at induction, at SIM hand-over or at the
        moment the customer asks for the transfer. Here it happens at startup
        so a judge can click Run without filling in a consent form first, and
        the scopes granted are exactly the ones the product's plan claims.
        """
        scopes = list(self.spec.consent.scopes)
        for profile in self.spec.all_lines():
            self.consent.grant(
                profile.msisdn,
                scopes,
                duration=timedelta(hours=12),
                basis=self.spec.consent.moment,
                reference="demo-enrolment",
            )

    def agent(self) -> Agent:
        return Agent(
            self.client,
            self.spec.policy,
            self.config.agent,
            ledger=self.ledger,
            events=self.events,
        )

    def record_decision(self, decision: Decision) -> None:
        """Expose effective planner provenance from the most recent case.

        ``configured_planner`` in :meth:`health` is intentionally separate:
        an invalid model key must never make a fallback decision look like a
        successful Gemini-planned one.
        """

        self._last_planner_run = {
            "case_id": decision.case_id,
            "planner": decision.planner,
            "planner_error": decision.planner_error,
            "created_at": decision.created_at,
        }

    def ensure_consent(self, subject: str) -> None:
        """Enrol a line nobody has enrolled yet.

        Checks history rather than current scopes on purpose: a line whose
        consent was explicitly withdrawn must stay withdrawn until someone
        grants it again, or the withdrawal would mean nothing.
        """
        if not self.consent.has_history(subject):
            self.consent.grant(
                subject,
                list(self.spec.consent.scopes),
                duration=timedelta(hours=12),
                basis="ad-hoc case",
                reference="api",
            )

    def health(self) -> Dict[str, Any]:
        agent_config = self.config.agent
        configured_planner = "gemini" if agent_config.llm_enabled else "policy"
        last = dict(self._last_planner_run)
        return {
            "product": self.spec.name,
            "slug": self.spec.slug,
            "theme": self.spec.theme_number,
            "network": self.client.describe(),
            "agent": {
                "configured_planner": configured_planner,
                "model": agent_config.model if agent_config.llm_enabled else None,
                "llm_configured": agent_config.llm_enabled,
                "last_decision_planner": last["planner"],
                "last_decision_model_error": last["planner_error"] or None,
                "last_decision_case_id": last["case_id"],
                "last_decision_at": last["created_at"],
                "model_verified": last["planner"] == "gemini",
                "max_steps": agent_config.max_steps,
                "budget_units": self.spec.policy.budget_units,
            },
            "tools": self.registry.names(),
            "camara_apis": self.registry.apis(),
            "consent": {
                "subjects": len(self.consent.subjects()),
                "scopes": list(self.spec.consent.scopes),
            },
            "ledger": self.ledger.stats().__dict__,
            "websocket_clients": self.events.subscriber_count(),
        }


def build_platform(spec: IdeaSpec, config: Optional[AppConfig] = None) -> Platform:
    return Platform(spec, config)


def create_app(spec: IdeaSpec, config: Optional[AppConfig] = None) -> FastAPI:
    platform = build_platform(spec, config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # The agent runs in a worker thread and publishes events from there, so
        # the bus needs a handle on the running loop before the first case.
        platform.events.bind_loop(asyncio.get_running_loop())
        try:
            yield
        finally:
            platform.client.close()

    app = FastAPI(
        title=spec.name + " - " + spec.tagline,
        description=spec.submission_description[:900],
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.platform = platform

    # -- pages ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(render_dashboard(spec))

    # -- metadata ------------------------------------------------------------

    @app.get("/api/health")
    async def health() -> Dict[str, Any]:
        return platform.health()

    @app.get("/api/spec")
    async def get_spec() -> Dict[str, Any]:
        return {
            **spec.metadata(),
            "tool_catalogue": platform.registry.catalogue(),
            "scenarios": [s.summary() for s in spec.scenarios],
            "cost_if_everything_called": platform.registry.total_cost_if_all_called(),
            "demo_lines": [
                {
                    "msisdn": p.msisdn,
                    "label": p.label,
                    "notes": p.notes,
                }
                for p in spec.all_lines()
            ],
        }

    # -- running cases -------------------------------------------------------

    @app.post("/api/run")
    async def run_scenario(body: RunScenario) -> Dict[str, Any]:
        scenario = spec.scenario(body.scenario_id)
        if scenario is None:
            raise HTTPException(404, "unknown scenario: " + body.scenario_id)
        return await asyncio.to_thread(_run_scenario, platform, scenario)

    @app.post("/api/run-all")
    async def run_all() -> Dict[str, Any]:
        return await asyncio.to_thread(_run_all, platform)

    @app.post("/api/case")
    async def run_case(body: AdHocCase) -> Dict[str, Any]:
        platform.ensure_consent(body.subject)
        # Geometry and default facts come from the product's own template, so a
        # reviewer can type a bare phone number and still get a real decision.
        case = spec.template_case(
            body.subject,
            facts=body.facts,
            params=body.params,
            latitude=body.latitude,
            longitude=body.longitude,
            radius_m=body.radius_m,
            label=body.label or "ad-hoc case",
        )
        decision = await asyncio.to_thread(platform.agent().run, case)
        platform.record_decision(decision)
        return decision.to_dict()

    # -- history and stats ---------------------------------------------------

    @app.get("/api/decisions")
    async def decisions(limit: int = 30, subject: Optional[str] = None) -> Dict[str, Any]:
        return {"decisions": platform.ledger.recent(limit=limit, subject=subject)}

    @app.get("/api/decisions/{case_id}")
    async def decision(case_id: str) -> Dict[str, Any]:
        found = platform.ledger.get_case(case_id)
        if found is None:
            raise HTTPException(404, "no such case")
        return found

    @app.get("/api/stats")
    async def stats() -> Dict[str, Any]:
        ledger_stats = platform.ledger.stats()
        everything = platform.registry.total_cost_if_all_called()
        avg = (
            round(ledger_stats.total_cost_units / ledger_stats.decisions, 2)
            if ledger_stats.decisions
            else 0.0
        )
        saved = round(100.0 * (1 - (avg / everything)), 1) if everything and avg else 0.0
        return {
            **ledger_stats.__dict__,
            "cost_if_everything_called": everything,
            "avg_cost_per_decision": avg,
            "saved_vs_calling_everything_pct": max(saved, 0.0),
        }

    # -- consent -------------------------------------------------------------

    @app.get("/api/consent")
    async def consent_state() -> Dict[str, Any]:
        return platform.consent.snapshot()

    @app.post("/api/consent/revoke")
    async def revoke(body: SubjectRef) -> Dict[str, Any]:
        removed = platform.consent.revoke(body.subject)
        platform.events.publish(
            "consent.revoked", {"subject": body.subject, "grants_withdrawn": removed}
        )
        return {
            "subject": body.subject,
            "grants_withdrawn": removed,
            "effect": "every CAMARA call for this line is now refused at the transport layer",
        }

    @app.post("/api/consent/grant")
    async def grant(body: SubjectRef) -> Dict[str, Any]:
        platform.consent.grant(
            body.subject,
            list(spec.consent.scopes),
            duration=timedelta(hours=12),
            basis=spec.consent.moment,
            reference="re-granted via api",
        )
        platform.events.publish("consent.granted", {"subject": body.subject})
        return {"subject": body.subject, "scopes": spec.consent.scopes}

    # -- simulator control ---------------------------------------------------

    @app.post("/api/simulator/update")
    async def simulator_update(body: SimulatorUpdate) -> Dict[str, Any]:
        profile = platform.simulator.update(body.subject, **body.changes)
        platform.events.publish(
            "simulator.updated", {"subject": body.subject, "changes": body.changes}
        )
        return {"subject": profile.msisdn, "label": profile.label, "applied": body.changes}

    @app.get("/api/simulator")
    async def simulator_state() -> Dict[str, Any]:
        return {
            "lines": [
                {
                    "msisdn": p.msisdn,
                    "label": p.label,
                    "number_verified": p.number_verified,
                    "sim_swap_hours_ago": p.sim_swap_hours_ago,
                    "device_swap_hours_ago": p.device_swap_hours_ago,
                    "reachability": p.reachability,
                    "roaming": p.roaming,
                    "country": p.country_name,
                    "congestion": p.congestion,
                    "notes": p.notes,
                }
                for p in platform.simulator.profiles.values()
            ]
        }

    @app.post("/api/reset")
    async def reset() -> Dict[str, Any]:
        platform.ledger.reset()
        platform.simulator = NetworkSimulator(spec.all_lines())
        platform.client.simulator = platform.simulator
        platform._last_planner_run = {
            "case_id": None,
            "planner": None,
            "planner_error": "",
            "created_at": None,
        }
        platform.events.publish("platform.reset", {})
        return {"reset": True}

    # -- live stream ---------------------------------------------------------

    @app.websocket("/ws")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = platform.events.subscribe()
        try:
            for event in platform.events.replay(60):
                await websocket.send_json(event)
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - a dropped socket is not an error
            pass
        finally:
            platform.events.unsubscribe(queue)

    @app.exception_handler(ValueError)
    async def value_error(_request: Any, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


def _run_scenario(platform: Platform, scenario: Any) -> Dict[str, Any]:
    """Apply a scenario's line profiles, then run its case."""
    if scenario.lines:
        platform.simulator.register_all(scenario.lines)
        for profile in scenario.lines:
            platform.ensure_consent(profile.msisdn)
    case = scenario.build_case()
    platform.ensure_consent(case.subject)
    decision = platform.agent().run(case)
    platform.record_decision(decision)
    payload = decision.to_dict()
    payload["scenario"] = scenario.summary()
    payload["met_expectation"] = decision.level == scenario.expect_level
    return payload


def _run_all(platform: Platform) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for scenario in platform.spec.scenarios:
        results.append(_run_scenario(platform, scenario))
    met = sum(1 for r in results if r.get("met_expectation"))
    total_cost = round(sum(r.get("budget_spent", 0) for r in results), 2)
    everything = platform.registry.total_cost_if_all_called() * len(results)
    return {
        "results": results,
        "scenarios_run": len(results),
        "expectations_met": met,
        "total_cost_units": total_cost,
        "cost_if_everything_called": round(everything, 2),
        "saved_pct": round(100.0 * (1 - total_cost / everything), 1) if everything else 0.0,
    }


def app_from_env(spec: IdeaSpec) -> FastAPI:
    """Entry point used by ``uvicorn main:app`` inside each bundle."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - dotenv is a convenience, not a dependency
        pass
    os.makedirs("data", exist_ok=True)
    return create_app(spec)
