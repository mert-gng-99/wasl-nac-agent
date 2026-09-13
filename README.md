# Wasl

> Network-backed proof of who is riding and what was delivered

**MENA Ignite Hackathon - GSMA Open Gateway - Theme 5: Industrial & Enterprise AI Automation**

Accounts get rented for the day and app locations can be faked. Wasl asks the network instead, and only about the riders worth asking about.

Wasl is an AI agent that moves two unverifiable proofs in delivery logistics onto the mobile network: that the approved rider is the person on the bike, and that the delivery happened where it was marked. It spends network checks only on the riders it has a reason to check.

---

## Quick start

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000. You need no credentials, because the app starts in
`simulator` mode and every answer is tagged with its source.

Full instructions, including the Gemini planner and the live Nokia gateway, are
in **INSTRUCTIONS.md**. The design is in **ARCHITECTURE.md**.

## What it is

An AI agent that decides *which* CAMARA network check is worth making for a
given case, spends against a budget, refuses calls it has no consent for, and
explains every decision with the network answers behind it.

- **8 scenarios** ship with it, all reaching the outcome they claim
- **8 CAMARA APIs** on the Nokia Network-as-Code platform
- **78.3% cheaper** than calling every available check on every case
- **0 to 4 calls** per case, depending on what the case deserves

## Scenarios

- Rider opens a shift. Nothing behind the account changed since yesterday (expects `verify`)
- The account changed hands overnight. Both the SIM and the handset changed since the last shift (expects `suspend`)
- A trusted rider marks a delivery complete. 2,100 deliveries, trust score 0.96, no dispute (expects `accept`)
- A newer rider marks a delivery complete. 310 deliveries, trust score 0.74, drop verified at the door (expects `verify`)
- The network answers PARTIAL. Rider in a tower lobby; the uncertainty circle straddles the door (expects `verify`)
- Marked complete 1.2 km from the door. No customer dispute, no device change (expects `challenge`)
- Disputed delivery from a swapped handset. 8 km from the door, customer disputing, handset changed this morning (expects `suspend`)
- A rider goes quiet. 14 minutes of silence, and the cell around the last ping is saturated (expects `incident`)

## CAMARA APIs used

| CAMARA API | What the agent asks it | Cost | Reveals |
| --- | --- | --- | --- |
| `number-verification` | Confirm the line on the phone | 1 | boolean |
| `sim-swap` | Has the SIM changed recently | 3 | boolean |
| `device-swap` | Has the handset changed recently | 3 | boolean |
| `device-status` | Can the line be reached | 1 | enum |
| `location-verification` | Is the line inside this area | 2 | boolean |
| `location-retrieval` | Where is the line | 4 | area |
| `congestion-insights` | How loaded is the serving cell | 1 | enum |
| `quality-on-demand` | Reserve network quality for this line | 8 | mutates |

## The agent

```
planner proposes one call  ->  runtime checks allowlist, consent, budget
      ^                                        |
      |                                        v
  answer becomes a fact   <-   CAMARA call recorded with provenance
      |
      +--> planner submits a decision  ->  policy floor applied  ->  ledger
```

The planner is Google AI Studio (Gemini) through Pydantic AI when
`AGENT_PROVIDER=gemini` and a `GEMINI_API_KEY` are both set, and a deterministic
policy ladder otherwise. Pydantic AI returns a typed proposal only; the runtime
still holds the budget, allowlist and consent gate, and the policy holds a floor
the model cannot talk its way under.

## Tests

```bash
pytest -q
```

## What this does not do

- Wasl proves which line was at the door. It does not prove which human held the phone, so a rider who hands their own phone to a friend is invisible to it.
- Location verification answers at cell and area resolution. A 150 m drop circle works in a dense city; a rural drop needs a wider circle and a weaker claim.
- PARTIAL answers are common in basements and towers, and the agent is deliberately generous with them. That means some genuinely false proofs will be accepted rather than risk docking honest riders' pay.
- Nothing here detects food quality, theft in transit, or a rider who delivers to the wrong flat in the right building.

## Layout

```
main.py            uvicorn entry point
app_spec.py        re-exports this product's spec
core/              shared platform: CAMARA client, agent, consent, ledger, UI
  camara.py        the eleven CAMARA API families, live + simulator
  simulator.py     deterministic network simulator
  agent.py         the agent loop, budget, guardrail
  tools.py         CAMARA tool registry with cost and reveal metadata
  consent.py       consent ledger enforced in the transport path
  ledger.py        SQLite decision ledger
  signals.py       CAMARA answers -> named facts
  server.py        FastAPI app
  webui.py         the operator console
idea/              this product: policy, scenarios, demo lines, copy
tests/             pytest suite
```

## Licence

MIT. See LICENSE.
