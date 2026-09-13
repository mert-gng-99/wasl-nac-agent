## Run it in two minutes (no credentials needed)

Requires Python 3.10 or newer.

```bash
git clone <REPOSITORY_URL>
cd wasl-nac-agent

python -m venv .venv
# Windows:       .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000**.

No API keys are required. The app starts in `simulator` mode, which answers
every CAMARA call in the real CAMARA response shape, because the hackathon
guidance recommends simulator numbers and a demo should not depend on a third
party being reachable. Every answer is tagged `simulator` in the UI.

## What to click, in order

1. Rider opens a shift, the first scenario on the left. Watch the agent pick
   CAMARA calls one at a time; the middle column fills with the decision, the
   step-by-step reasoning, and the network evidence table.
2. Run all eight rider events runs all 8 scenarios back to back and shows the
   cost comparison: what the agent spent against what calling every available
   check on every case would have cost.
3. **Withdraw consent**, then run any scenario again. The agent is refused at
   the transport layer and makes **zero** CAMARA calls. Press **Restore** to
   put it back. This is the consent gate, and it is enforced in code rather
   than promised in a document.
4. Ad-hoc check. Type any phone number. Unregistered lines get a stable
   profile derived from the number itself, so a reviewer can experiment and
   still get reproducible answers.

## Run the tests

```bash
pytest -q
```

Covers the CAMARA transport and its provenance, the consent gate, budget
enforcement, the tool allowlist, the guardrail that overrules an over-confident
model, the Pydantic AI planner loop against a scripted runner, the whole HTTP surface,
and all 8 scenarios reaching the outcome they claim.

## Turn on the Gemini planner (optional)

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Then set **both** of these in `.env`:

```
AGENT_PROVIDER=gemini
GEMINI_API_KEY=<your key from aistudio.google.com>
```

```bash
uvicorn main:app --port 8000
```

`/api/health` will report `"configured_planner": "gemini"`; that confirms only
that a model is configured. Run a scenario next. The proof of a real model turn
is `"planner": "gemini"` in that decision and `"model_verified": true` in
`/api/health`. If the decision says `"policy-fallback"`, correct the key/model
before recording or submitting the demo.

Both settings are required on purpose: a key alone leaves the deterministic
planner in charge, so nothing starts spending on a model just because a key is
present in the environment. That deterministic planner runs the same escalation
ladder, which is why the prototype works offline and the tests are stable.

## Point it at the real Nokia gateway (optional)

Self-register at **networkascode.nokia.io**, then in `.env`:

```
NAC_MODE=live
NAC_RAPIDAPI_KEY=<your key>
NAC_DEV_MODE=true
```

`NAC_DEV_MODE=true` targets the sandbox hosts that answer for simulator
MSISDNs. Use `NAC_MODE=hybrid` to keep a demo available if a live call fails,
but do not call that a live proof: the individual evidence record must show
`"source": "live"`. A hybrid fallback is visibly tagged `"simulator"`.

## Prove the deployed demonstration

After you have set both Gemini variables and the Nokia credentials, run this
from the repository root (take `SCENARIO_ID` from `GET /api/spec`):

```bash
python scripts/verify-live-demo.py https://your-app.onrender.com SCENARIO_ID
```

It sends one normal scenario request and exits successfully only when Gemini
actually planned that decision, `/api/health` confirms it, and at least one
returned CAMARA evidence record is genuinely tagged `live`. It prints `READY`
when the exact proof needed for the video is present; do not record a live-API
claim until it does.

## Deploy

The repository ships a `Dockerfile` and a `render.yaml`:

```bash
docker build -t wasl . && docker run -p 8000:8000 wasl
```

Or point Render at the repo and it reads `render.yaml`. Any host that runs a
container works; the app needs one writable directory for its SQLite ledger.

## API surface

`GET /api/health`, `GET /api/spec`, `POST /api/run`, `POST /api/run-all`,
`POST /api/case`, `GET /api/decisions`, `GET /api/stats`, `GET /api/consent`,
`POST /api/consent/revoke`, `POST /api/simulator/update`, `WS /ws`.
Interactive docs at **/docs**.
