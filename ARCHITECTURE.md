# Wasl - architecture

## Why this shape

The hackathon asks for an AI agent layer that orchestrates CAMARA APIs as
trusted real-time data sources rather than treating them as user-triggered
actions. The distinction we took seriously is *choice*: anyone can call a SIM
swap API when a button is pressed. Deciding whether this particular case is
worth the call is where an agent earns its place, so the whole design is built
around that decision and around being able to justify it afterwards.

## Layers

```
  HTTP / WebSocket          core/server.py, core/webui.py
        |
  the agent loop            core/agent.py       budget, steps, guardrail
        |
  tool registry             core/tools.py       cost, latency, reveal level
        |
  consent gate              core/consent.py     enforced here, not in policy docs
        |
  CAMARA client             core/camara.py      11 API families, provenance on every call
        |
  live gateway  /  simulator                    core/simulator.py
```

Product-specific logic lives in `idea/`: the policy (which questions to ask in
what order, and what the facts mean), the scenarios, the demo lines and the
words the operator sees. Everything else is shared. An eighth vertical is a
policy and a spec, not another application. That is the claim this layout
exists to make true.

## The agent loop

Each iteration:

1. **Plan.** The planner is handed the case, the facts established so far, the
   checks already run and the remaining budget, and proposes exactly one move:
   a tool call with a reason, or a decision.
2. **Check.** The runtime validates the move against the tool allowlist, the
   consent ledger and the budget. A refusal is recorded as a step with its
   reason and fed back to the planner, which can then act on it.
3. **Observe.** The CAMARA call runs. The answer is stored as an `ApiResult`
   with the API, endpoint, latency, source, request digest and cost, then
   `idea/policy.py` turns it into named facts.
4. **Stop.** When the planner submits a decision, the budget is exhausted, a
   tool is proposed twice (which means the run has stalled on a fact it cannot
   obtain), or the step ceiling is reached.

### Two planners, one interface

`LlmPlanner` is a Pydantic AI structured-output planner backed by Google AI
Studio (Gemini). It receives the tool catalogue, cost, latency and reveal level
and returns one validated proposal: an allowed next check or a decision. It
does not receive executable CAMARA functions. That deliberate boundary means
the model cannot bypass the consent, budget, provenance or argument guardrails
held by the runtime.

`PolicyPlanner` walks the product's escalation ladder deterministically. It
runs when no model key is configured and is what the tests assert against.

Having both is not hedging. A judged demo must not fail because a model
endpoint is slow, and a test suite must not depend on sampling.

### The guardrail

`policy.decide(case, facts)` computes an outcome from the facts alone. At
finalisation the runtime compares the planner's level against that floor, and
if the planner is less cautious the floor wins, with the disagreement written
into `guardrail_applied` and `guardrail_note` on the decision record.

This is the answer to the obvious objection about putting a language model in a
decision path. The model's job is allocating spend across checks. The model
cannot clear a case the evidence condemns, and `tests/test_agent.py` proves it
with a scripted model that tries.

## Consent

Consent is in the transport path. `CamaraClient._call` takes a scope and a
subject, and asks the `ConsentLedger` before building a request; an ungranted
call raises `ConsentError`. Grants are scoped by which family of calls, which
line, and a time window, so access ends when the shift, trip or
transfer ends.

For Wasl: at induction, as one clear line in the rider contract pack, from the rider, who is an enrolled worker and the owner of the line on file. Checks run during a booked shift and during an open incident, never outside one. No track of the rider's day is ever built.

`POST /api/consent/revoke` exists so this can be demonstrated rather than
asserted. Withdraw consent, re-run a case, and the agent makes zero calls.

## The ledger

Every decision is written to SQLite with the calls behind it, the checks the
agent *declined* to buy and why, the planner used, and the budget spent. Three
reasons that is core rather than logging: a held transfer or a flagged exam has
to be explainable to the person affected months later; the judging criteria ask
for orchestration that can be shown; and the decision *not* to spend is the
product, so it has to be recorded as carefully as the calls.

## CAMARA integration

Requests and responses follow the CAMARA specs as published on the Nokia
gateway. `POST /check` for SIM swap, `POST /verify` for location verification,
`POST /connectivity` and `POST /roaming` for device status, and so on. The
client is a thin, original `httpx` integration so this project retains its own
consent gate, request digest and per-call provenance rather than delegating
those safety-critical decisions to a vendor wrapper.

Nokia fronts each CAMARA API on its own RapidAPI host, with `X-RapidAPI-Key`
and `X-RapidAPI-Host` headers; `core/config.py` holds the production and
sandbox hosts and the path prefixes.

### Ordering by what a call reveals

Every tool declares a reveal level: `boolean` answers a yes/no question,
`enum` returns a small status, `area` hands back a place, `mutates` changes the
network. Policies are written to ask the least revealing question that could
still change the outcome. Location verification before location retrieval, not
because it is cheaper, though it is, but because it hands back no position.

## The simulator

`core/simulator.py` answers every CAMARA call in the real response shape,
driven by a `LineProfile` per MSISDN. A line nobody registered gets a stable
pseudo-profile derived from a hash of its own number, so a reviewer can type
anything and still get reproducible behaviour.

It deliberately produces CAMARA's uncertain answers. `PARTIAL`, the line's
uncertainty circle straddling the boundary, is a real answer that most demos
pretend does not exist, and how a policy handles it is where the difference
between a careful product and a careless one shows up.

Simulated answers are tagged `source: "simulator"` by the client and rendered
with that tag in the UI. Nothing here can be mistaken for a real network answer.

## What we would change for production

- The consent ledger is in memory. It belongs in the same database as the
  enrolment record, with an audit trail of grants and withdrawals.
- SQLite is right for a prototype and wrong for a fleet. The ledger interface
  is narrow enough to swap.
- Geofencing subscriptions need a real webhook endpoint with signature
  verification; the prototype points them at an example sink.
- Thresholds in `idea/policy.py` are ours, set to make behaviour legible. Real
  deployment sets them from the customer's own history.
