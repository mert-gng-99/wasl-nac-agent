"""Tests for the agent runtime.

These cover the guarantees the runtime makes regardless of which product's
policy is loaded, and regardless of whether a language model is planning: the
budget holds, the allowlist holds, the consent gate holds, and a planner cannot
talk its way past the evidence.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from core.agent import Agent, Budget, Case, LlmPlanner, PlannedMove, PolicyPlanner
from core.camara import CamaraClient
from core.config import AgentConfig, NacConfig
from core.consent import ConsentLedger
from core.simulator import LineProfile, NetworkSimulator
from idea import SPEC

POLICY = SPEC.policy
SUBJECT = "+900000009999"


def _platform(profile: LineProfile, scopes=None):
    sim = NetworkSimulator(SPEC.all_lines() + [profile])
    ledger = ConsentLedger()
    ledger.grant(
        profile.msisdn,
        scopes if scopes is not None else list(SPEC.consent.scopes),
        duration=timedelta(hours=1),
    )
    client = CamaraClient(NacConfig(mode="simulator"), simulator=sim, consent=ledger)
    return client, ledger


def _any_case(subject: str = SUBJECT) -> Case:
    """A case built from the product's own first scenario, retargeted.

    Using the real scenario keeps these tests honest across all seven products
    without each one needing its own fixture.
    """
    template = SPEC.scenarios[0].build_case()
    return Case(
        subject=subject,
        kind=template.kind,
        facts=dict(template.facts),
        latitude=template.latitude,
        longitude=template.longitude,
        radius_m=template.radius_m,
        params=dict(template.params),
        label="runtime test",
    )


# --- budget ------------------------------------------------------------------


def test_budget_arithmetic():
    budget = Budget(limit_units=10)
    assert budget.can_afford(10) and not budget.can_afford(10.01)
    budget.charge(7)
    assert budget.remaining == 3
    assert budget.can_afford(3) and not budget.can_afford(4)


def test_budget_ceiling_is_never_exceeded():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    config = AgentConfig(provider="policy", max_steps=12)
    agent = Agent(client, POLICY, config)
    decision = agent.run(_any_case())
    assert decision.budget_spent <= POLICY.budget_units


def test_a_starved_budget_still_produces_a_decision():
    """Zero budget must not hang or crash - it must decide on nothing."""

    class Starved:
        name = "policy"
        levels = POLICY.levels
        kind = POLICY.kind
        tool_names = POLICY.tool_names
        budget_units = 0.0

        def system_prompt(self, case): return POLICY.system_prompt(case)
        def describe_case(self, case): return POLICY.describe_case(case)
        def interpret(self, tool, result, facts): return POLICY.interpret(tool, result, facts)
        def next_tool(self, case, facts, used): return POLICY.next_tool(case, facts, used)
        def decide(self, case, facts): return POLICY.decide(case, facts)

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    agent = Agent(client, Starved(), AgentConfig(provider="policy"))
    decision = agent.run(_any_case())
    assert decision.level in POLICY.levels
    assert decision.evidence == []
    assert decision.skipped, "a refused call should be recorded, not silently dropped"


# --- allowlist and consent ---------------------------------------------------


def test_tools_outside_the_allowlist_are_refused_not_executed():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)

    class Greedy(PolicyPlanner):
        def plan(self, case, facts, used, budget, registry):
            return SimpleNamespace(
                kind="tool", tool="attach_slice_nonexistent", args={}, reasoning="try it"
            )

    agent = Agent(client, POLICY, AgentConfig(provider="policy", max_steps=2),
                  planner=Greedy(POLICY))
    decision = agent.run(_any_case())
    assert decision.evidence == []
    assert any("not available" in s.get("reason", "") for s in decision.skipped)


def test_model_cannot_supply_location_geometry_or_ledger_arguments():
    """Geometry belongs to the case, never to a planner's function arguments."""

    class LocationPolicy:
        name = "location guardrail"
        levels = ("accept", "review")
        kind = "location_test"
        tool_names = ("verify_location", "watch_area")
        budget_units = 10.0

        def interpret(self, tool, result, facts):
            return {}

        def decide(self, case, facts):
            return ("accept", "record", "The case geometry is authoritative.", 0.9)

    class HostilePlanner:
        def __init__(self):
            self.moves = [
                PlannedMove(
                    kind="tool",
                    tool="verify_location",
                    args={"latitude": 0, "longitude": 0, "radius_m": 1},
                    reasoning="try to substitute a different place",
                ),
                PlannedMove(
                    kind="tool",
                    tool="watch_area",
                    args={"event": "entered", "radius_m": 999999},
                    reasoning="try to widen the watched area",
                ),
                PlannedMove(
                    kind="decide",
                    level="accept",
                    action="record",
                    rationale="finished location checks",
                    confidence=0.9,
                ),
            ]

        def plan(self, *args):
            return self.moves.pop(0)

    profile = LineProfile(
        msisdn=SUBJECT,
        latitude=41.0,
        longitude=29.0,
        location_accuracy_m=20,
    )
    simulator = NetworkSimulator([profile])
    ledger = ConsentLedger()
    ledger.grant(
        SUBJECT,
        ["location:verify", "location:geofence"],
        duration=timedelta(hours=1),
    )
    client = CamaraClient(
        NacConfig(mode="simulator"), simulator=simulator, consent=ledger
    )
    agent = Agent(
        client,
        LocationPolicy(),
        AgentConfig(provider="policy", max_steps=4),
        planner=HostilePlanner(),
    )
    decision = agent.run(
        Case(
            subject=SUBJECT,
            kind="location_test",
            latitude=41.0,
            longitude=29.0,
            radius_m=200,
        )
    )

    # Decision.evidence and Decision.steps are serialised dicts, because the
    # decision record has to survive a round trip through the ledger and the
    # WebSocket unchanged.
    assert decision.evidence[0]["data"]["verificationResult"] == "TRUE"
    area = decision.evidence[1]["data"]["config"]["subscriptionDetail"]["area"]
    assert area["center"] == {"latitude": 41.0, "longitude": 29.0}
    assert area["radius"] == 200
    # The hostile planner asked for (0, 0) at 1 m and then a 999 km fence. The
    # case geometry above is what actually reached the network, and what the
    # ledger recorded.
    assert decision.steps[0]["args"] == {}
    assert decision.steps[1]["args"] == {"event": "entered"}

    from core.tools import ToolRegistry

    declarations = ToolRegistry(["verify_location", "watch_area"]).declarations()
    assert declarations[0]["parameters"]["properties"] == {}
    assert set(declarations[1]["parameters"]["properties"]) == {"event"}


def test_gemini_requires_an_explicit_provider_opt_in(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)

    offline = AgentConfig.from_env()
    assert offline.provider == "policy"
    assert offline.llm_enabled is False
    # The pinned model tracks whatever Google AI Studio currently serves.
    # gemini-2.5-flash, which the tooling guide names, now answers 404.
    assert offline.model == "gemini-3.5-flash"

    monkeypatch.setenv("AGENT_PROVIDER", "gemini")
    enabled = AgentConfig.from_env()
    assert enabled.provider == "gemini"
    assert enabled.llm_enabled is True


def test_withheld_consent_blocks_every_call():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile, scopes=[])
    agent = Agent(client, POLICY, AgentConfig(provider="policy", max_steps=6))
    decision = agent.run(_any_case())
    assert decision.evidence == [], "no CAMARA call may happen without consent"
    assert decision.level in POLICY.levels


def test_a_stalled_planner_does_not_burn_every_step():
    """Repeatedly proposing the same refused tool must end the run."""
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile, scopes=[])
    agent = Agent(client, POLICY, AgentConfig(provider="policy", max_steps=8))
    decision = agent.run(_any_case())
    assert len(decision.steps) <= 3
    assert "step ceiling" not in decision.rationale


# --- the guardrail floor -----------------------------------------------------


class ScriptedPydanticRunner:
    """A tiny Pydantic AI ``run_sync`` double with typed-output payloads."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.prompts = []
        self.settings = []

    def run_sync(self, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        self.settings.append(kwargs.get("model_settings"))
        output = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(output, BaseException):
            raise output
        return SimpleNamespace(output=output)


def test_llm_planner_drives_real_tool_calls():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    first_tool = POLICY.tool_names[0]
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=4)
    runner = ScriptedPydanticRunner(
        [
            {"kind": "tool", "tool": first_tool, "args": {}, "reasoning": "start with evidence"},
            {
                "kind": "decide",
                "level": POLICY.levels[0],
                "action": "proceed",
                "rationale": "the model's own words",
                "confidence": 0.9,
                "reasoning": "enough evidence",
            },
        ]
    )
    planner = LlmPlanner(POLICY, config, runner=runner)
    agent = Agent(client, POLICY, config, planner=planner)
    decision = agent.run(_any_case())
    assert decision.planner == "gemini"
    assert planner.last_error == "", "the scripted model path must not fall back"
    assert [s["tool"] for s in decision.steps if s["kind"] == "tool"] == [first_tool]
    assert runner.calls == 2
    assert "CAMARA CHECK CATALOGUE" in runner.prompts[0]
    assert runner.settings == [{"temperature": config.temperature}] * 2


def test_invalid_model_tool_uses_explicit_policy_fallback():
    """An unavailable model tool cannot turn into a falsely Gemini-labelled case."""

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=4)
    runner = ScriptedPydanticRunner(
        [{"kind": "tool", "tool": "not_a_real_camara_tool", "reasoning": "bad proposal"}]
    )
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert decision.planner == "policy-fallback"
    assert "unavailable CAMARA tool" in decision.planner_error
    assert runner.calls == 1


def test_repeated_model_tool_uses_explicit_policy_fallback():
    """A repeated model tool is not a model decision when the runtime stops it."""

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    first_tool = POLICY.tool_names[0]
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=4)
    runner = ScriptedPydanticRunner(
        [
            {"kind": "tool", "tool": first_tool, "reasoning": "first check"},
            {"kind": "tool", "tool": first_tool, "reasoning": "repeat it"},
        ]
    )
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert decision.planner == "policy-fallback"
    assert "already completed CAMARA tool" in decision.planner_error
    assert runner.calls == 2


def test_model_step_ceiling_uses_explicit_policy_fallback():
    """The runtime's own final decision at the step ceiling is policy provenance."""

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=1)
    runner = ScriptedPydanticRunner(
        [{"kind": "tool", "tool": POLICY.tool_names[0], "reasoning": "one more check"}]
    )
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert decision.planner == "policy-fallback"
    assert "step ceiling" in decision.planner_error
    assert runner.calls == 1


@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        (
            {"kind": "decide", "level": POLICY.levels[0]},
            "incomplete decision proposal",
        ),
        (
            {
                "kind": "decide",
                "level": "not-an-allowed-level",
                "action": "review manually",
                "rationale": "the model chose an unsupported level",
                "confidence": 0.8,
            },
            "unsupported decision level",
        ),
        (
            {
                "kind": "decide",
                "level": POLICY.levels[0],
                "action": "review manually",
                "rationale": "the model omitted its confidence",
            },
            "incomplete decision proposal",
        ),
    ],
)
def test_incomplete_or_invalid_model_decision_uses_policy_fallback(proposal, reason):
    """Only a complete, policy-valid model decision can carry Gemini provenance."""

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=4)
    runner = ScriptedPydanticRunner([proposal])
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert decision.planner == "policy-fallback"
    assert reason in decision.planner_error
    assert runner.calls == 1


def test_model_error_never_reflects_the_configured_api_key():
    """The dashboard/ledger error is auditable, but it is never a secret sink."""

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    secret = "test-secret-that-must-not-be-shown"
    config = AgentConfig(provider="gemini", api_key=secret, max_steps=4)
    runner = ScriptedPydanticRunner([RuntimeError("provider rejected " + secret)])
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert decision.planner == "policy-fallback"
    assert secret not in decision.planner_error
    assert "[redacted]" in decision.planner_error


def test_the_floor_overrides_an_over_confident_model():
    """A model that clears a case the evidence condemns must be overruled."""
    worst = POLICY.levels[-1]

    class Strict:
        name = "strict"
        levels = POLICY.levels
        kind = POLICY.kind
        tool_names = POLICY.tool_names
        budget_units = POLICY.budget_units

        def system_prompt(self, case): return "test"
        def describe_case(self, case): return "test"
        def interpret(self, tool, result, facts): return {}
        def next_tool(self, case, facts, used): return None
        def decide(self, case, facts):
            return (worst, "escalate", "the evidence demands it", 0.9)

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=3)
    planner = LlmPlanner(
        Strict(),
        config,
        runner=ScriptedPydanticRunner(
            [{"kind": "decide",
                "level": POLICY.levels[0],
                "action": "all fine",
                "rationale": "nothing to see",
                "confidence": 0.99,
            }]
        ),
    )
    agent = Agent(client, Strict(), config, planner=planner)
    decision = agent.run(_any_case())
    assert decision.level == worst
    assert decision.guardrail_applied is True
    assert POLICY.levels[0] in decision.guardrail_note


def test_a_broken_model_falls_back_instead_of_failing_the_case():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=6)
    runner = ScriptedPydanticRunner([RuntimeError("model unavailable")])
    planner = LlmPlanner(POLICY, config, runner=runner)
    agent = Agent(client, POLICY, config, planner=planner)
    decision = agent.run(_any_case())
    assert decision.level in POLICY.levels
    assert "model unavailable" in planner.last_error
    # A configured Gemini key is not evidence that Gemini actually planned the
    # case. The decision, dashboard payload and ledger record must say so.
    assert decision.planner == "policy-fallback"
    assert "model unavailable" in decision.planner_error
    assert decision.to_dict()["planner"] == "policy-fallback"
    assert "model unavailable" in decision.to_dict()["planner_error"]
    # Once one model call fails, this case stays on the deterministic path;
    # otherwise a mixed run could later be presented as a successful model run.
    assert runner.calls == 1


# --- the record --------------------------------------------------------------


def test_every_decision_is_auditable():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)
    agent = Agent(client, POLICY, AgentConfig(provider="policy"))
    decision = agent.run(_any_case())

    assert decision.case_id and decision.subject == SUBJECT
    assert decision.rationale, "a decision nobody can explain is not shippable"
    assert decision.action
    assert decision.level in POLICY.levels
    assert 0.0 <= decision.confidence <= 1.0
    for call in decision.evidence:
        assert call["api"] and call["endpoint"] and call["source"] in {"live", "simulator"}
    payload = decision.to_dict()
    assert set(payload) >= {
        "level", "action", "rationale", "planner", "planner_error",
        "evidence", "steps", "skipped",
    }


def test_policy_levels_are_ordered_and_unique():
    assert len(POLICY.levels) == len(set(POLICY.levels))
    assert len(POLICY.levels) >= 2
    # Every level must have a style, or the dashboard cannot render it.
    styled = SPEC.ui.level_map()
    for level in POLICY.levels:
        assert level in styled, "level '%s' has no UI style" % level


def test_tool_names_all_exist():
    from core.tools import ToolRegistry

    registry = ToolRegistry(list(POLICY.tool_names))
    assert len(registry) == len(POLICY.tool_names)
    assert registry.apis(), "a product with no CAMARA APIs is not a submission"


# --- transient provider faults ----------------------------------------------


def test_only_server_side_faults_are_retried():
    """A rate limit must not be retried; an overloaded provider should be."""
    from core.agent import _is_transient

    class ProviderError(Exception):
        def __init__(self, status=None, message=""):
            super().__init__(message)
            if status is not None:
                self.status_code = status

    assert _is_transient(ProviderError(503, "overloaded")) is True
    assert _is_transient(ProviderError(500, "internal")) is True
    assert _is_transient(ProviderError(429, "rate limited")) is False
    assert _is_transient(ProviderError(404, "model retired")) is False

    # Pydantic AI reports the status inside the message, so the text path has
    # to make the same distinction.
    assert _is_transient(Exception("ModelHTTPError: status_code: 503, model_name: x"))
    assert not _is_transient(Exception("ModelHTTPError: status_code: 429, RESOURCE_EXHAUSTED"))
    assert not _is_transient(Exception("ModelHTTPError: status_code: 404, no longer available"))
    assert not _is_transient(ValueError("model proposed an unavailable CAMARA tool"))


def test_a_transient_fault_is_retried_once_then_still_falls_back_honestly():
    """One retry, and if it fails again the run is labelled a fallback."""
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)

    class Overloaded:
        def __init__(self):
            self.attempts = 0

        def run_sync(self, *args, **kwargs):
            self.attempts += 1
            raise RuntimeError("ModelHTTPError: status_code: 503, model_name: test")

    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=2)
    runner = Overloaded()
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert runner.attempts >= 2, "a 503 should be retried once before giving up"
    assert decision.planner == "policy-fallback"
    assert "503" in decision.planner_error


def test_a_rate_limit_is_not_retried():
    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)

    class RateLimited:
        def __init__(self):
            self.attempts = 0

        def run_sync(self, *args, **kwargs):
            self.attempts += 1
            raise RuntimeError("ModelHTTPError: status_code: 429, RESOURCE_EXHAUSTED")

    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=2)
    runner = RateLimited()
    planner = LlmPlanner(POLICY, config, runner=runner)
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert runner.attempts == 1, "a quota error must not be hammered"
    assert decision.planner == "policy-fallback"


def test_the_model_schema_only_permits_this_policy_ladder():
    """A real run once proposed 'allow'. The schema must make that impossible."""
    from core.agent import _proposal_model_for

    Proposal = _proposal_model_for(tuple(POLICY.levels))
    schema = Proposal.model_json_schema()
    assert schema["properties"]["level"]["enum"] == list(POLICY.levels)

    ok = Proposal(kind="decide", level=POLICY.levels[-1], action="act", rationale="why")
    assert ok.level == POLICY.levels[-1]

    with pytest.raises(Exception):
        Proposal(kind="decide", level="allow", action="act", rationale="why")


def test_a_model_that_invents_a_level_no_longer_costs_the_whole_case():
    """With the ladder in the schema, the planner still runs on a valid level."""
    from types import SimpleNamespace

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)

    class Decides:
        def run_sync(self, *args, **kwargs):
            return SimpleNamespace(output={
                "kind": "decide",
                "level": POLICY.levels[-1],
                "action": "escalate",
                "rationale": "the model decided this",
                "confidence": 0.9,
            })

    config = AgentConfig(provider="gemini", api_key="test-key", max_steps=3)
    planner = LlmPlanner(POLICY, config, runner=Decides())
    decision = Agent(client, POLICY, config, planner=planner).run(_any_case())

    assert decision.planner == "gemini"
    assert decision.planner_error == ""
    assert decision.level == POLICY.levels[-1]


def test_a_planner_cannot_skip_a_check_the_policy_still_requires():
    """An evidence floor, to match the outcome floor.

    A live Gemini run answered on turn one with nothing gathered, which lands
    on the no-evidence verdict instead of the real one.
    """
    from types import SimpleNamespace

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)

    class DecidesImmediately:
        def __init__(self):
            self.turns = 0

        def plan(self, case, facts, used, budget, registry):
            self.turns += 1
            return PlannedMove(
                kind="decide",
                level=POLICY.levels[0],
                action="skip everything",
                rationale="deciding without looking",
                confidence=0.9,
            )

    config = AgentConfig(provider="policy", max_steps=6)
    decision = Agent(client, POLICY, config, planner=DecidesImmediately()).run(_any_case())

    # The first scenario of every product is one the policy wants a check for,
    # so the early decision must have been held back at least once.
    assert decision.evidence, "the policy's required check was skipped"
    assert any(
        "still requires this check" in (s.get("reasoning") or "")
        for s in decision.steps
    )


def test_an_early_decision_stands_when_the_policy_wants_nothing():
    """Restraint is still allowed: no required check means decide now."""
    from types import SimpleNamespace

    class NothingRequired:
        name = "nothing required"
        levels = POLICY.levels
        kind = POLICY.kind
        tool_names = POLICY.tool_names
        budget_units = POLICY.budget_units

        def system_prompt(self, case): return "test"
        def describe_case(self, case): return "test"
        def interpret(self, tool, result, facts): return {}
        def next_tool(self, case, facts, used): return None
        def decide(self, case, facts):
            return (POLICY.levels[0], "nothing to do", "no check was worth buying", 0.9)

    profile = LineProfile(msisdn=SUBJECT, latitude=25.0, longitude=55.0)
    client, _ = _platform(profile)

    class DecidesImmediately:
        def plan(self, case, facts, used, budget, registry):
            return PlannedMove(
                kind="decide",
                level=POLICY.levels[0],
                action="nothing to do",
                rationale="nothing could change the answer",
                confidence=0.9,
            )

    decision = Agent(
        client, NothingRequired(), AgentConfig(provider="policy", max_steps=4),
        planner=DecidesImmediately(),
    ).run(_any_case())

    assert decision.evidence == [], "no check was required, so none should run"
    assert decision.level == POLICY.levels[0]
