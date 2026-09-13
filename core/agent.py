"""The AI agent layer.

Every product in this platform is the same agent with a different policy. The
agent's job is not to call CAMARA APIs; it is to decide *which* network
question is worth asking about this particular case, stop as soon as the answer
cannot change, and then justify what it did.

The loop
--------

1.  A planner is asked for the next move given the case and the answers so far.
2.  The runtime - not the planner - checks the move against the tool allowlist,
    the consent ledger and the remaining budget.
3.  The tool runs, the CAMARA answer is recorded with full provenance, and the
    policy turns it into a fact.
4.  Repeat until the planner submits a decision, the budget runs out, or the
    step ceiling is hit.

Two planners, one interface
---------------------------

``LlmPlanner`` is the real agent layer: Gemini returns a typed next proposal,
reasoning about cost and about how much each check reveals.
``PolicyPlanner`` is a deterministic fallback that follows the same interface,
so the prototype still runs with no model key and the test suite has something
stable to assert against.

Guardrails over trust
---------------------

The policy computes a floor for every case from the facts alone. If the model
proposes something less cautious than that floor, the floor wins and the
disagreement is written into the decision record. A language model should be
able to choose which checks to buy; it should not be able to clear a fraud
case that the evidence says to hold.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .camara import ApiResult, CamaraClient, ConsentError, Device
from .config import AgentConfig
from .tools import Tool, ToolContext, ToolRegistry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- budget ------------------------------------------------------------------


@dataclass
class Budget:
    """What this case is allowed to cost.

    Enforced by the runtime. A planner that asks for a call it cannot afford is
    refused and told why, which is information it can act on.
    """

    limit_units: float
    spent_units: float = 0.0

    @property
    def remaining(self) -> float:
        return round(self.limit_units - self.spent_units, 3)

    def can_afford(self, cost: float) -> bool:
        return self.spent_units + cost <= self.limit_units + 1e-9

    def charge(self, cost: float) -> None:
        self.spent_units = round(self.spent_units + cost, 3)


# --- case and decision -------------------------------------------------------


@dataclass
class Case:
    """One thing to decide about."""

    subject: str                         # the MSISDN under consideration
    kind: str = "case"
    case_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    facts: Dict[str, Any] = field(default_factory=dict)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_m: int = 1000
    params: Dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def device(self) -> Device:
        return Device(phone_number=self.subject)

    def context(self) -> ToolContext:
        return ToolContext(
            device=self.device(),
            latitude=self.latitude,
            longitude=self.longitude,
            radius_m=self.radius_m,
            params=dict(self.params),
        )


@dataclass
class AgentStep:
    n: int
    kind: str                            # tool | refused | decide | error
    tool: str = ""
    api: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    observation: Dict[str, Any] = field(default_factory=dict)
    cost_units: float = 0.0
    latency_ms: int = 0
    source: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Decision:
    case_id: str
    subject: str
    kind: str
    level: str
    action: str
    rationale: str
    confidence: float = 0.0
    budget_spent: float = 0.0
    budget_limit: float = 0.0
    planner: str = "policy"
    # Empty for a normal policy/Gemini run. When the model fails, this makes
    # the displayed ``policy-fallback`` provenance auditable without leaking a
    # credential or request body.
    planner_error: str = ""
    apis_used: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)
    guardrail_applied: bool = False
    guardrail_note: str = ""
    label: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- policy interface --------------------------------------------------------


class AgentPolicy(Protocol):
    """What an idea must supply to get an agent.

    Deliberately small. A new vertical is a policy, not a new agent.
    """

    name: str
    kind: str
    tool_names: List[str]
    budget_units: float
    levels: List[str]

    def system_prompt(self, case: Case) -> str: ...
    def describe_case(self, case: Case) -> str: ...
    def interpret(self, tool: str, result: ApiResult, facts: Dict[str, Any]) -> Dict[str, Any]: ...
    def next_tool(self, case: Case, facts: Dict[str, Any], used: List[str]) -> Optional[Tuple[str, Dict[str, Any], str]]: ...
    def decide(self, case: Case, facts: Dict[str, Any]) -> Tuple[str, str, str, float]: ...


# --- planners ----------------------------------------------------------------


@dataclass
class PlannedMove:
    """A planner's proposed next move."""

    kind: str                            # "tool" | "decide"
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    level: str = ""
    action: str = ""
    rationale: str = ""
    confidence: float = 0.0
    # Checks the planner wanted but did not propose, and why. Restraint is the
    # product here, so a check declined on price has to be as visible in the
    # record as one that ran.
    declined: List[Dict[str, Any]] = field(default_factory=list)


class _ModelProposal(BaseModel):
    """The only thing the model is allowed to return.

    This is intentionally a proposal rather than a CAMARA function.  Gemini can
    choose the next permitted check, but it cannot invoke a network API or
    mutate the consent/ledger state.  The runtime below remains the only tool
    executor.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["tool", "decide"] = Field(
        description="Whether to propose one check or submit the case decision."
    )
    tool: str = Field(
        default="",
        description="Exact name of one check in the supplied CAMARA catalogue when kind is tool.",
    )
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Only declared arguments for the selected check. Omit protected geometry and identifiers.",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this is the least revealing useful next move.",
    )
    level: str = Field(default="", description="Policy outcome level when kind is decide.")
    action: str = Field(default="", description="Concrete human next action when kind is decide.")
    rationale: str = Field(default="", description="Evidence-based decision explanation when kind is decide.")
    confidence: float = Field(default=0.7, description="Decision confidence from zero to one.")


class PolicyPlanner:
    """Deterministic planner driven by the idea's own escalation ladder.

    Runs when no model key is configured, and is what the test suite asserts
    against. It follows the same cheap-and-least-revealing-first discipline the
    LLM is instructed to follow, so the two planners are comparable.
    """

    name = "policy"

    def __init__(self, policy: AgentPolicy) -> None:
        self.policy = policy

    def plan(
        self,
        case: Case,
        facts: Dict[str, Any],
        used: List[str],
        budget: Budget,
        registry: ToolRegistry,
    ) -> PlannedMove:
        declined: List[Dict[str, Any]] = []
        proposal = self.policy.next_tool(case, facts, used)
        if proposal is not None:
            tool_name, args, reason = proposal
            tool = registry.get(tool_name)
            if budget.can_afford(tool.cost_units):
                return PlannedMove(kind="tool", tool=tool_name, args=args, reasoning=reason)
            # The ladder wanted this check and the budget would not cover it.
            # That is a decision, so it goes on the record rather than being
            # quietly dropped on the way to a verdict.
            declined.append(
                {
                    "tool": tool.name,
                    "api": tool.api,
                    "reason": "costs %.0f units, only %.1f left in this case's budget"
                    % (tool.cost_units, budget.remaining),
                }
            )

        level, action, rationale, confidence = self.policy.decide(case, facts)
        return PlannedMove(
            kind="decide",
            level=level,
            action=action,
            rationale=rationale,
            confidence=confidence,
            declined=declined,
        )


class LlmPlanner:
    """Gemini planning through Pydantic AI's typed structured-output path.

    Pydantic AI asks Gemini for one :class:`_ModelProposal` per turn.  The
    proposal names a possible check but is deliberately *not* a callable
    CAMARA tool: consent, budget, allowlist and argument filtering remain in
    :meth:`Agent._run_tool`.  This prevents the model integration from gaining
    an alternate path around the runtime guardrails.

    ``client`` is retained as a backwards-compatible injection point for tests;
    it must implement Pydantic AI's ``run_sync`` runner shape.  New code should
    use the clearer ``runner`` keyword.
    """

    def __init__(
        self,
        policy: AgentPolicy,
        config: AgentConfig,
        client: Any = None,
        *,
        runner: Any = None,
    ) -> None:
        if client is not None and runner is not None:
            raise ValueError("pass either client or runner, not both")
        self.policy = policy
        self.config = config
        self._injected_runner = runner if runner is not None else client
        self._runner = self._injected_runner
        self._observations: List[Dict[str, Any]] = []
        self._fallback = PolicyPlanner(policy)
        self.last_error: str = ""
        self.fallback_used = False

    @property
    def name(self) -> str:
        """The planner that actually governed this run's final decision."""

        return "policy-fallback" if self.fallback_used else "gemini"

    def begin_run(self) -> None:
        """Reset per-case state when an injected planner is reused in tests/UI."""

        self._observations = []
        self.last_error = ""
        self.fallback_used = False
        # A normal Agent creates a new planner per case. Clearing this cache
        # also makes an explicitly re-used LlmPlanner pick up the next case's
        # policy instruction, without discarding a test runner.
        if self._injected_runner is None:
            self._runner = None

    def mark_runtime_fallback(self, reason: str) -> None:
        """Record a policy completion the runtime, rather than Gemini, made.

        A model may successfully answer individual planning turns yet fail to
        submit a usable decision before the runtime has to stop a stalled or
        step-limited case.  That result must not be displayed as a Gemini
        decision merely because the earlier turns came from Gemini.
        """

        if self.fallback_used:
            return
        self.last_error = self._bounded_error("runtime completion: %s" % reason)
        self.fallback_used = True

    def _bounded_error(self, detail: Any) -> str:
        """Keep audit detail useful without ever reflecting a configured key."""

        message = str(detail)
        if self.config.api_key:
            message = message.replace(self.config.api_key, "[redacted]")
        return message[:240]

    # -- model plumbing ------------------------------------------------------

    def _runner_for(self, case: Case) -> Any:
        if self._runner is not None:
            return self._runner
        try:
            from pydantic_ai import Agent as PydanticAgent
            from pydantic_ai import ModelSettings
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider
        except ImportError as exc:  # pragma: no cover - exercised by deployment dependency check
            raise RuntimeError(
                "Pydantic AI with Google support is required for AGENT_PROVIDER=gemini"
            ) from exc

        provider = GoogleProvider(api_key=self.config.api_key)
        model = GoogleModel(self.config.model, provider=provider)
        self._runner = PydanticAgent(
            model,
            output_type=_ModelProposal,
            instructions=self._instruction(case),
            # A failed structured response should enter the explicit policy
            # fallback below, not make unbounded hidden model retries.
            retries=0,
        )
        # Keep the import used and make the intended supported request setting
        # discoverable next to construction.  The instance itself is applied in
        # _plan_with_model so each turn observes the configured temperature.
        self._model_settings_type = ModelSettings
        return self._runner

    def _instruction(self, case: Case) -> str:
        return (
            self.policy.system_prompt(case)
            + "\n\nYou are a planner, not a network client. Return exactly one structured "
            "proposal. A tool proposal must name exactly one supplied CAMARA check and only "
            "its declared arguments. Never invent a tool, invoke an API, alter consent, "
            "supply device identifiers, or supply location geometry. A decision proposal must "
            "name one allowed level, a concrete action, rationale and confidence. Prefer the "
            "cheapest, least revealing check that could still change the outcome; decide as "
            "soon as another check cannot change it."
        )

    def plan(
        self,
        case: Case,
        facts: Dict[str, Any],
        used: List[str],
        budget: Budget,
        registry: ToolRegistry,
    ) -> PlannedMove:
        if self.fallback_used:
            return self._fallback_move(case, facts, used, budget, registry)
        try:
            return self._plan_with_model(case, facts, used, budget, registry)
        except Exception as exc:  # noqa: BLE001 - a demo must not die on the model
            # A free-tier provider returns 503 under load often enough that one
            # retry is the difference between a judge seeing the model plan and
            # seeing the deterministic fallback. Retry only server-side faults:
            # a rate limit or a bad request will not heal by being repeated, and
            # hammering a quota is worse than falling back.
            if _is_transient(exc):
                time.sleep(1.5)
                try:
                    return self._plan_with_model(case, facts, used, budget, registry)
                except Exception as retry_exc:  # noqa: BLE001
                    exc = retry_exc
            self.last_error = self._bounded_error("%s: %s" % (type(exc).__name__, exc))
            # Once the model fails, keep the case entirely on the deterministic
            # path. A later recovery must not make a mixed run look like a
            # successful Gemini decision in the ledger or dashboard.
            self.fallback_used = True
            return self._fallback_move(case, facts, used, budget, registry)

    def _fallback_move(
        self,
        case: Case,
        facts: Dict[str, Any],
        used: List[str],
        budget: Budget,
        registry: ToolRegistry,
    ) -> PlannedMove:
        move = self._fallback.plan(case, facts, used, budget, registry)
        move.reasoning = (
            "model planner unavailable (%s); fell back to the deterministic "
            "policy ladder" % self.last_error
        )
        return move

    def _plan_with_model(
        self,
        case: Case,
        facts: Dict[str, Any],
        used: List[str],
        budget: Budget,
        registry: ToolRegistry,
    ) -> PlannedMove:
        affordable = [t.name for t in registry.tools if budget.can_afford(t.cost_units)]
        turn = (
            "CASE\n%s\n\nFACTS ESTABLISHED SO FAR\n%s\n\nNETWORK ANSWERS SO FAR\n%s\n\n"
            "CHECKS ALREADY RUN\n%s\n\nBUDGET\n%.1f of %.1f units spent, %.1f remaining.\n"
            "Affordable checks: %s\n\nCAMARA CHECK CATALOGUE (descriptions only; do not invoke it)\n%s\n\n"
            "Return one structured proposal: either one affordable check that could still change "
            "the outcome, or a decision now."
            % (
                self.policy.describe_case(case),
                json.dumps(facts, indent=2, default=str) if facts else "(nothing yet)",
                json.dumps(self._observations, indent=2, default=str) if self._observations else "(none)",
                ", ".join(used) if used else "(none)",
                budget.spent_units,
                budget.limit_units,
                budget.remaining,
                ", ".join(affordable) if affordable else "(none - you must decide now)",
                json.dumps(registry.declarations(), indent=2, default=str),
            )
        )

        runner = self._runner_for(case)
        settings_type = getattr(self, "_model_settings_type", None)
        if settings_type is None:
            # Injected test runners do not need Pydantic AI installed. A plain
            # dict is accepted by the public run_sync API as model settings.
            model_settings: Any = {"temperature": self.config.temperature}
        else:
            model_settings = settings_type(temperature=self.config.temperature)
        result = runner.run_sync(turn, model_settings=model_settings)
        raw_output = getattr(result, "output", None)
        if raw_output is None:
            raise RuntimeError("Pydantic AI runner returned no structured proposal")
        proposal = (
            raw_output
            if isinstance(raw_output, _ModelProposal)
            else _ModelProposal.model_validate(raw_output)
        )
        move = self._proposal_to_move(proposal)
        if move.kind == "tool":
            try:
                tool = registry.get(move.tool)
            except KeyError as exc:
                raise ValueError(
                    "model proposed an unavailable CAMARA tool %r" % move.tool
                ) from exc
            if move.tool in used:
                raise ValueError(
                    "model repeated an already completed CAMARA tool %r" % move.tool
                )
            if not budget.can_afford(tool.cost_units):
                raise ValueError(
                    "model proposed an over-budget CAMARA tool %r" % move.tool
                )
        return move

    def _proposal_to_move(self, proposal: _ModelProposal) -> PlannedMove:
        if proposal.kind == "tool":
            if not proposal.tool:
                raise ValueError("model submitted a tool proposal without a tool name")
            return PlannedMove(
                kind="tool",
                tool=proposal.tool,
                args=dict(proposal.args),
                reasoning=proposal.reasoning,
            )
        missing = [
            name
            for name in ("level", "action", "rationale", "confidence")
            if name not in proposal.model_fields_set or not str(getattr(proposal, name)).strip()
        ]
        if missing:
            raise ValueError(
                "model submitted an incomplete decision proposal: missing " + ", ".join(missing)
            )
        if proposal.level not in self.policy.levels:
            raise ValueError(
                "model submitted an unsupported decision level %r" % proposal.level
            )
        confidence = float(proposal.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("model submitted a decision confidence outside 0..1")
        return PlannedMove(
            kind="decide",
            level=proposal.level,
            action=proposal.action,
            rationale=proposal.rationale,
            confidence=confidence,
            reasoning=proposal.reasoning,
        )

    def observe(self, tool_name: str, payload: Dict[str, Any]) -> None:
        """Make runtime-controlled tool observations available on the next turn."""

        self._observations.append({"tool": tool_name, "result": payload})


# --- the agent ---------------------------------------------------------------


class Agent:
    """Runs one case to a decision."""

    def __init__(
        self,
        client: CamaraClient,
        policy: AgentPolicy,
        config: AgentConfig,
        *,
        ledger: Any = None,
        events: Any = None,
        planner: Any = None,
    ) -> None:
        self.client = client
        self.policy = policy
        self.config = config
        self.ledger = ledger
        self.events = events
        self.registry = ToolRegistry(list(policy.tool_names))
        self._planner_override = planner

    def _new_planner(self):
        if self._planner_override is not None:
            return self._planner_override
        if self.config.llm_enabled:
            return LlmPlanner(self.policy, self.config)
        return PolicyPlanner(self.policy)

    def _emit(self, topic: str, payload: Dict[str, Any]) -> None:
        if self.events is not None:
            try:
                self.events.publish(topic, payload)
            except Exception:  # noqa: BLE001 - never let the dashboard break a decision
                pass

    def run(self, case: Case) -> Decision:
        planner = self._new_planner()
        begin_run = getattr(planner, "begin_run", None)
        if callable(begin_run):
            begin_run()
        budget = Budget(limit_units=self.policy.budget_units)
        facts: Dict[str, Any] = dict(case.facts)
        used: List[str] = []
        steps: List[AgentStep] = []
        evidence: List[ApiResult] = []
        skipped: List[Dict[str, Any]] = []
        ctx = case.context()
        started = time.perf_counter()

        self._emit(
            "case.started",
            {"case_id": case.case_id, "subject": case.subject, "kind": case.kind, "label": case.label},
        )

        final: Optional[PlannedMove] = None
        attempted: List[str] = []
        stalled = False
        for step_no in range(1, self.config.max_steps + 1):
            move = planner.plan(case, facts, used, budget, self.registry)

            if move.kind == "decide":
                final = move
                skipped.extend(move.declined)
                steps.append(
                    AgentStep(
                        n=step_no,
                        kind="decide",
                        reasoning=move.reasoning,
                        note="planner submitted a decision",
                    )
                )
                break

            # A tool that was already attempted will answer the same way twice.
            # Asking again is a loop - it happens when consent is withheld and
            # the planner keeps waiting for a fact it can never get - so the
            # second attempt ends the run instead of burning every step.
            if move.tool in attempted:
                stalled = True
                self._mark_runtime_fallback(
                    planner,
                    "the model repeated a tool before submitting a valid decision",
                )
                steps.append(
                    AgentStep(
                        n=step_no,
                        kind="refused",
                        tool=move.tool,
                        reasoning=move.reasoning,
                        note="already attempted this run; deciding on what is known",
                    )
                )
                break
            attempted.append(move.tool)

            step = self._run_tool(step_no, move, ctx, budget, facts, used, evidence, skipped, planner)
            steps.append(step)
            self._emit("case.step", {"case_id": case.case_id, **step.to_dict()})

        if final is None:
            # Either the run stalled on a fact it could not get, or it hit the
            # step ceiling. Decide from what is known rather than hanging, and
            # only say "ceiling" when that is actually what happened.
            self._mark_runtime_fallback(
                planner,
                (
                    "the model did not submit a valid decision before a repeated tool stalled the run"
                    if stalled
                    else "the model did not submit a valid decision before the step ceiling"
                ),
            )
            level, action, rationale, confidence = self.policy.decide(case, facts)
            final = PlannedMove(
                kind="decide",
                level=level,
                action=action,
                rationale=rationale if stalled else rationale + " (step ceiling reached)",
                confidence=confidence,
            )

        decision = self._finalise(
            case, final, facts, budget, steps, evidence, skipped, planner
        )
        decision.facts = dict(facts)

        if self.ledger is not None:
            try:
                self.ledger.record(decision)
            except Exception:  # noqa: BLE001
                pass

        self._emit(
            "case.decided",
            {
                **decision.to_dict(),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return decision

    def _run_tool(
        self,
        step_no: int,
        move: PlannedMove,
        ctx: ToolContext,
        budget: Budget,
        facts: Dict[str, Any],
        used: List[str],
        evidence: List[ApiResult],
        skipped: List[Dict[str, Any]],
        planner: Any,
    ) -> AgentStep:
        # Allowlist. A planner naming a tool this product does not use is a
        # refusal, not an error to crash on.
        try:
            tool: Tool = self.registry.get(move.tool)
        except KeyError as exc:
            skipped.append({"tool": move.tool, "reason": "not available to this agent"})
            return AgentStep(
                n=step_no, kind="refused", tool=move.tool, reasoning=move.reasoning, note=str(exc)
            )

        if not budget.can_afford(tool.cost_units):
            skipped.append(
                {
                    "tool": tool.name,
                    "api": tool.api,
                    "reason": "costs %.0f units, only %.1f left" % (tool.cost_units, budget.remaining),
                }
            )
            self._observe(planner, tool.name, {"refused": "over budget", "remaining": budget.remaining})
            return AgentStep(
                n=step_no,
                kind="refused",
                tool=tool.name,
                api=tool.api,
                reasoning=move.reasoning,
                note="refused: over budget",
            )

        args = tool.allowed_args(move.args)
        try:
            result = tool.run(self.client, ctx, args)
        except ConsentError as exc:
            skipped.append({"tool": tool.name, "api": tool.api, "reason": "no consent: %s" % exc})
            self._observe(planner, tool.name, {"refused": "no consent for this scope"})
            return AgentStep(
                n=step_no,
                kind="refused",
                tool=tool.name,
                api=tool.api,
                reasoning=move.reasoning,
                note="refused: no active consent for %s" % tool.consent_scope,
            )
        except Exception as exc:  # noqa: BLE001 - a failed call is a fact too
            skipped.append({"tool": tool.name, "api": tool.api, "reason": str(exc)[:160]})
            self._observe(planner, tool.name, {"error": str(exc)[:160]})
            return AgentStep(
                n=step_no,
                kind="error",
                tool=tool.name,
                api=tool.api,
                reasoning=move.reasoning,
                note=str(exc)[:200],
            )

        budget.charge(tool.cost_units)
        used.append(tool.name)
        evidence.append(result)
        observation = self.policy.interpret(tool.name, result, facts) or {}
        facts.update(observation)
        self._observe(planner, tool.name, {"answer": result.data, "derived": observation})

        return AgentStep(
            n=step_no,
            kind="tool",
            tool=tool.name,
            api=tool.api,
            args=args,
            reasoning=move.reasoning,
            observation=observation,
            cost_units=tool.cost_units,
            latency_ms=result.latency_ms,
            source=result.source,
        )

    @staticmethod
    def _observe(planner: Any, tool_name: str, payload: Dict[str, Any]) -> None:
        if hasattr(planner, "observe"):
            planner.observe(tool_name, payload)

    @staticmethod
    def _mark_runtime_fallback(planner: Any, reason: str) -> None:
        """Tell model planners when the runtime had to complete the case."""

        mark = getattr(planner, "mark_runtime_fallback", None)
        if callable(mark):
            mark(reason)

    def _finalise(
        self,
        case: Case,
        move: PlannedMove,
        facts: Dict[str, Any],
        budget: Budget,
        steps: List[AgentStep],
        evidence: List[ApiResult],
        skipped: List[Dict[str, Any]],
        planner: Any,
    ) -> Decision:
        levels = list(self.policy.levels)
        floor_level, floor_action, floor_rationale, floor_conf = self.policy.decide(case, facts)

        level = move.level if move.level in levels else floor_level
        action = move.action or floor_action
        rationale = move.rationale or floor_rationale
        confidence = move.confidence or floor_conf

        guardrail = False
        note = ""
        if levels.index(level) < levels.index(floor_level):
            # The planner was less cautious than the evidence allows.
            guardrail = True
            note = (
                "planner proposed '%s'; the evidence supports at least '%s', so the "
                "more cautious level stands" % (level, floor_level)
            )
            level, action = floor_level, floor_action
            rationale = floor_rationale
            confidence = max(confidence, floor_conf)

        planner_name = getattr(planner, "name", "policy")
        planner_error = (
            str(getattr(planner, "last_error", ""))[:240]
            if getattr(planner, "fallback_used", False)
            else ""
        )
        return Decision(
            case_id=case.case_id,
            subject=case.subject,
            kind=case.kind or self.policy.kind,
            level=level,
            action=action,
            rationale=rationale,
            confidence=round(float(confidence), 3),
            budget_spent=budget.spent_units,
            budget_limit=budget.limit_units,
            planner=planner_name,
            planner_error=planner_error,
            apis_used=_unique([r.api for r in evidence]),
            evidence=[r.to_dict() for r in evidence],
            skipped=skipped,
            steps=[s.to_dict() for s in steps],
            guardrail_applied=guardrail,
            guardrail_note=note,
            label=case.label,
        )


# Server-side faults worth one retry. 429 is deliberately absent: a rate limit
# is the provider asking for less traffic, not more.
_TRANSIENT_STATUS = (500, 502, 503, 504)


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return False
    return any("status_code: %d" % code in text for code in _TRANSIENT_STATUS)


def _unique(items: List[str]) -> List[str]:
    seen: List[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
