## Wasl - Network-backed proof of who is riding and what was delivered

Wasl is an AI agent that moves two unverifiable proofs in delivery logistics onto the mobile network: that the approved rider is the person on the bike, and that the delivery happened where it was marked. It spends network checks only on the riders it has a reason to check.

### The problem

Accounts get rented for the day and app locations can be faked. Wasl asks the network instead, and only about the riders worth asking about.

### What the prototype actually does

Wasl is a working web application with an operator console, a REST API, and
a live WebSocket feed of the agent's reasoning. Open it, click a scenario, and
you watch the agent choose CAMARA calls one at a time and then justify its
decision with the network answers behind it.

It runs in three modes. `simulator` needs no credentials and answers every
CAMARA call in the real CAMARA response shape, which is how the organisers
recommend demonstrating and how the test suite stays deterministic. `live`
calls the Nokia Network-as-Code gateway with your own key. `hybrid` uses live
where credentials allow and falls back per call. Every answer is tagged with
its source in the UI, so a simulated result can never pass itself off as a real
network answer.

### The AI agent layer

The agent is a planner over a CAMARA tool registry, not a script with an LLM
bolted on. Each tool in the registry carries its price, its typical latency and
how much it reveals about a person, and the planner is judged on choosing well:

1. The planner proposes one call, with a stated reason.
2. The runtime, never the model, checks it against the tool allowlist, the
   consent ledger and the remaining budget.
3. The CAMARA answer is recorded with full provenance and turned into a fact.
4. Repeat until the planner submits a decision, or the budget runs out.

The planner is Google AI Studio (Gemini) through **Pydantic AI**'s typed,
structured-output path. It is enabled by setting `AGENT_PROVIDER=gemini`
alongside a `GEMINI_API_KEY`. A model turn may only propose a next CAMARA check
or a decision; it cannot execute a network call itself. The runtime remains the
only executor of consent, the tool allowlist, argument filtering and budget.

Gemini is opt-in on both counts deliberately: a key sitting in the environment
should not be enough to start spending on a model. Otherwise a deterministic
policy planner implementing the same escalation ladder takes over, so the
prototype is demonstrable offline and CI has something stable to assert. If a
configured model cannot complete a turn, the finished case is explicitly
labelled `policy-fallback` with a bounded error reason. It is never presented
as a successful Gemini-planned decision.

**The guardrail is the part worth looking at.** The policy computes a floor for
every case from the facts alone. If the model proposes something less cautious
than the floor, the floor wins and the disagreement is written into the
decision record. A language model should choose which checks to buy; it should
not be able to clear a case the evidence says to escalate. There is a test for
exactly this.

### Results from the shipped scenarios

8 scenarios ship with the prototype, and all 8 reach the
outcome they claim. The demo and the test suite assert the same thing, so a
scenario drifting from the pitch is a build failure.

- Outcome levels reached: `accept`, `verify`, `challenge`, `incident`, `suspend`
- CAMARA calls per case: 0 to 4 (average 1.9)
- Total spend across all scenarios: 40 units, against 184 if
  every available check were called on every case, a saving of 78.3%

| Scenario | Outcome | CAMARA calls | Spend |
| --- | --- | --- | --- |
| Rider opens a shift | `verify` | 2 | 4 |
| The account changed hands overnight | `suspend` | 3 | 7 |
| A trusted rider marks a delivery complete | `accept` | 0 | 0 |
| A newer rider marks a delivery complete | `verify` | 1 | 2 |
| The network answers PARTIAL | `verify` | 2 | 6 |
| Marked complete 1.2 km from the door | `challenge` | 1 | 2 |
| Disputed delivery from a swapped handset | `suspend` | 2 | 5 |
| A rider goes quiet | `incident` | 4 | 14 |

The cheapest case, *A trusted rider marks a delivery complete*, resolves in 0 call(s). The
most expensive, *A rider goes quiet*, earns 4. That gap is the product:
an agent that calls everything on everyone is safe, useless and unaffordable.

### CAMARA APIs on Nokia Network as Code

`number-verification`, `sim-swap`, `device-swap`, `device-status`, `location-verification`, `location-retrieval`, `congestion-insights`, `quality-on-demand`

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

### Consent

CAMARA identity, location and geofencing APIs are only lawful with the consent
of the line owner, so consent is enforced in the transport path rather than
described in a policy document. An ungranted call raises before a request is
built.

Consent is taken at induction, as one clear line in the rider contract pack,
from the rider, who is an enrolled worker and the owner of the line on file.
Checks run during a booked shift and during an open incident, never outside
one. No track of the rider's day is ever built. A rider can withdraw at any
time; the platform then falls back to its existing app-based proof for that
rider.

You can prove this in the running app: press **Withdraw consent**, run the same
case again, and watch the agent get refused at the transport layer with zero
CAMARA calls made.

### What this does not do

- Wasl proves which line was at the door. It does not prove which human held the phone, so a rider who hands their own phone to a friend is invisible to it.
- Location verification answers at cell and area resolution. A 150 m drop circle works in a dense city; a rural drop needs a wider circle and a weaker claim.
- PARTIAL answers are common in basements and towers, and the agent is deliberately generous with them. That means some genuinely false proofs will be accepted rather than risk docking honest riders' pay.
- Nothing here detects food quality, theft in transit, or a rider who delivers to the wrong flat in the right building.

### Who pays

- Delivery platforms and courier firms paying a monthly price per active rider
- Logistics fleets who need proof of handover for B2B parcels
- Mobile operators, who share the API income and can sell Wasl with the fleet SIM plan

### Verification

Run `pytest -q` in the repository. The suite covers the CAMARA transport and
its provenance, the consent gate, budget enforcement, the tool allowlist, the
guardrail floor overruling an over-confident model, the LLM planner loop
against a scripted model, the full HTTP surface, and every shipped scenario.
