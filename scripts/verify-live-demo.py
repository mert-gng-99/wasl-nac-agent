#!/usr/bin/env python3
"""Verify a deployed Gemini + Nokia NaC demonstration.

Usage:
    python scripts/verify-live-demo.py https://your-app.onrender.com SCENARIO_ID

This sends one normal demo request. It succeeds only if that decision was
actually planned by Gemini (not policy fallback) and contains at least one
evidence record tagged ``source: live``. It never prints API keys or raw model
prompts.
"""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(base: str, path: str, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=data, headers=headers, method="POST" if data else "GET")
    # A free instance plus several model turns comfortably exceeds 30s.
    with urlopen(req, timeout=180) as response:  # nosec B310 - explicit demo URL
        return json.loads(response.read().decode("utf-8"))


def fail(message: str) -> int:
    print("NOT READY: " + message, file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: verify-live-demo.py https://your-app.onrender.com SCENARIO_ID", file=sys.stderr)
        return 2
    base = argv[0].rstrip("/")
    scenario_id = argv[1]
    if not base.startswith(("https://", "http://")):
        return fail("demo URL must start with http:// or https://")

    try:
        before = request(base, "/api/health")
        agent = before.get("agent", {})
        if agent.get("configured_planner") != "gemini":
            return fail("AGENT_PROVIDER=gemini and GEMINI_API_KEY are not both active")

        decision = request(base, "/api/run", {"scenario_id": scenario_id})
        if decision.get("planner") != "gemini":
            return fail("decision used %r: %s" % (
                decision.get("planner"), decision.get("planner_error") or "no provider detail",
            ))
        if decision.get("planner_error"):
            return fail("decision has a Gemini provider error")
        after = request(base, "/api/health").get("agent", {})
        if after.get("last_decision_planner") != "gemini" or after.get("model_verified") is not True:
            return fail("health did not confirm the successful Gemini decision")

        sources = sorted({item.get("source") for item in decision.get("evidence", []) if item.get("source")})
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return fail("could not verify deployed demo: %s" % exc)

    # Two independent proofs. The agent layer is proven by a real model turn,
    # the network layer by a live CAMARA answer, and a deployment can honestly
    # have one without the other. Reporting them together hides which half is
    # actually missing.
    print("AGENT READY: Gemini planned %s and the runtime accepted its decision." % scenario_id)
    if "live" in sources:
        print("NETWORK READY: Nokia NaC returned live evidence (%s)." % ", ".join(sources))
        return 0
    print(
        "NETWORK NOT PROVEN: evidence came from %s. Set NAC_MODE=hybrid and "
        "NAC_RAPIDAPI_KEY on the deployment to show live CAMARA answers too."
        % ", ".join(sources)
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
