# Wasl - Prototype Phase submission

Copy each block into the matching field. Four fields need something only you
can provide; they are marked **YOU** and listed again at the bottom.

---

## Title

```
Wasl - network backed proof of who is riding and what was delivered
```

---

## Description

The full text is in SUBMISSION_DESCRIPTION.md in this folder. Paste that
file's contents. It is Markdown and the field accepts formatting.

---

## Parent Submission

**YOU.** Pick your Idea Phase submission for Wasl from the dropdown.
If you submitted all seven ideas, the parent is the one titled close to:

```
Wasl - network backed proof of who is riding and what was delivered
```

---

## Theme

```
5. Industrial & Enterprise AI Automation
```

---

## Snapshots

Upload the PNGs from the `snapshots/` folder in this bundle. If it is empty or
you want fresher ones, take three with the app running at
http://localhost:8000:

1. The dashboard after Run all eight rider events, showing the batch table and the cost comparison.
2. A single decision with its step list and evidence table visible.
3. The consent gate after Withdraw consent, with zero calls and the refusals listed.

JPG, JPEG or PNG, under 3MB each.

---

## Video URL

**YOU.** Record it and paste the link (YouTube unlisted is fine).
A shot-by-shot 3-minute script is in **DEMO_SCRIPT.md**.

---

## Presentation

Upload:

```
Wasl Pitch Deck.pptx
```

It sits in the parent folder of this bundle. Update the closing slides with
the prototype numbers before uploading: 8 scenarios, all passing,
8 CAMARA APIs, 78.3% cheaper than calling every check.

---

## Demo Link

**YOU.** The deployed URL. The admin confirmed in the discussion thread that
the project must be deployed, so this cannot be a localhost link.

Fastest route, using the `render.yaml` in this bundle:

1. Push this folder to GitHub (see `scripts/init-repo.sh`).
2. render.com -> New -> Blueprint -> pick the repo -> Apply.
3. Paste the resulting `https://....onrender.com` URL here.

It needs no API keys, because simulator mode runs out of the box. Free tiers
sleep after inactivity, so open the link once shortly before judging.

---

## Repository URL

Already created and pushed. Paste:

```
https://github.com/mert-gng-99/wasl-nac-agent
```

---

## Source Code

Upload:

```
wasl-nac-agent-source.zip
```

It is in the `dist/` folder next to this bundle, well under the 50MB limit.

---

## Instructions to Run

The full text is in INSTRUCTIONS_TO_RUN.md in this folder. Paste that
file's contents.

---

# What is left for you

| Field | What is needed | Time |
| --- | --- | --- |
| Parent Submission | Pick Wasl from the dropdown | seconds |
| Demo Link | Deploy, using the Render blueprint in the bundle | ~10 min |
| Video URL | Record the 3-minute script in DEMO_SCRIPT.md | ~20 min |
| Repository URL | Done. The repo is created and pushed. | done |

Snapshots are optional but cheap, and a judge scrolling a list of submissions
sees them before they read anything.

---

## Cross-check against the mandatory requirements

| Requirement | How this bundle meets it |
| --- | --- |
| At least one CAMARA API on Nokia NaC | 8 API families: `number-verification`, `sim-swap`, `device-swap`, `device-status`, `location-verification`, `location-retrieval`, `congestion-insights`, `quality-on-demand` |
| An AI agent layer orchestrating them | `core/agent.py`. Pydantic AI with Google AI Studio (Gemini) returns one typed, cost-aware CAMARA proposal per turn, and the runtime applies consent, allowlist, argument and budget guardrails before it executes anything |
| Agent built only with approved tooling | Google AI Studio (Gemini) and Pydantic AI are the guide-listed AI/agent tools used by this submission. The Google provider dependency is supplied by Pydantic AI; no additional AI framework or model provider is used. |
| Original code | Every line in `core/` and `idea/` was written for this hackathon. The CAMARA client is written directly against the published endpoints rather than wrapping the vendor SDK. |
| Solves a real problem | Industrial & Enterprise AI Automation. Stated limits are in README.md, so this is not pitched as doing more than it does. |
| Theme alignment | Theme 5 |
| Multiple CAMARA APIs (good-to-have) | 8 families orchestrated per case |
| Intelligent orchestration (good-to-have) | 0 to 4 calls depending on the case; 78.3% cheaper than calling everything |
| Production-minded (good-to-have) | Consent in the transport path, evidence ledger, Docker and Render config, 8 scenario tests plus transport, agent and API suites |

Built 2026-09-13.
