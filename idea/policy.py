"""Wasl - network-backed proof of who is riding and what was delivered.

Delivery platforms trust two things they cannot check. The first is that the
approved rider is the person on the bike; accounts get rented for the day, so
the platform sees a checked worker while the street sees someone nobody
checked. The second is the delivery itself, because proof comes from software
on the rider's own phone and a free app can move that location.

Wasl moves both proofs to the network. Three decisions shape this policy.

**Trust is earned, and checks cost money.** A rider with two thousand clean
deliveries does not get his drop verified. The agent spends on the riders it
has a reason to spend on, and the ledger shows how few that is.

**An uncertain answer is not an accusation.** CAMARA location verification can
answer PARTIAL, which means the line's uncertainty circle straddles the
boundary. A rider in a tower block lobby looks exactly like that. The agent
buys the retrieved area before it decides anyone faked a delivery.

**A silent line is a question, not a verdict.** When a rider stops answering,
the first thing to establish is whether the phone is dead or the rider is. That
is a reachability call, and it costs one unit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.agent import Case
from core.camara import ApiResult
from core.signals import read_signal

# Ordered by how much human attention the outcome demands.
LEVELS = ["accept", "verify", "challenge", "incident", "suspend"]

# A rider above this, with no open dispute, is taken at their word.
TRUSTED_SCORE = 0.90
TRUSTED_DELIVERIES = 500
# How far outside the drop circle a retrieved area may still sit before the
# proof is treated as contradicted rather than merely imprecise.
LOBBY_TOLERANCE_M = 400


class WaslPolicy:
    name = "wasl"
    kind = "rider_event"
    levels = LEVELS
    budget_units = 22.0

    tool_names = [
        "verify_number",
        "check_sim_swap",
        "check_device_swap",
        "check_reachability",
        "verify_location",
        "retrieve_location",
        "query_congestion",
        "reserve_quality",
    ]

    def system_prompt(self, case: Case) -> str:
        return (
            "You are Wasl, the agent inside a delivery platform's operations "
            "system. You handle three kinds of event: a rider starting a shift, "
            "a rider marking a delivery complete, and a rider who has gone "
            "quiet.\n\n"
            "Principles, in order:\n"
            "1. Trust is earned and checks cost money. A rider with a long clean "
            "record and no dispute on this order needs no network check at all. "
            "Checking everyone is the failure mode, not the safe option.\n"
            "2. When you do check a delivery, ask the yes/no area question "
            "first. It returns no coordinates and costs half what retrieving a "
            "position costs.\n"
            "3. PARTIAL and UNKNOWN are real answers and they are not evidence "
            "of fraud. A rider in a tower lobby or a basement car park answers "
            "PARTIAL. Buy the retrieved area before you contradict anyone.\n"
            "4. A SIM change and a device change together, between two shifts, "
            "means the account changed hands. That is the one finding that ends "
            "a shift immediately.\n"
            "5. For a silent rider, establish whether the phone is reachable "
            "before anything else. A dead battery and a crash look identical on "
            "a dispatch board and could not be more different.\n\n"
            "Riders are contracted workers who consented at induction, and "
            "checks only run during a shift. Never build a picture of a rider's "
            "day; answer the question in front of you and stop."
        )

    def describe_case(self, case: Case) -> str:
        f = case.facts
        event = f.get("event", "delivery_complete")
        lines = [
            "Rider event: %s" % event,
            "  rider: %s (line %s)" % (f.get("rider_name", "unknown"), case.subject),
            "  deliveries completed: %s" % f.get("deliveries_completed", "unknown"),
            "  trust score: %s" % f.get("trust_score", "unknown"),
        ]
        if event == "shift_start":
            lines.append("  hours since last shift: %s" % f.get("hours_since_last_shift", "unknown"))
        if event == "delivery_complete":
            lines.append("  order value: %s" % f.get("order_value", "unknown"))
            lines.append("  customer disputed: %s" % bool(f.get("customer_disputed")))
            lines.append("  drop point given, radius %sm" % case.radius_m)
        if event == "rider_silent":
            lines.append("  minutes since last ping: %s" % f.get("silent_minutes", "unknown"))
        return "\n".join(lines)

    def interpret(self, tool: str, result: ApiResult, facts: Dict[str, Any]) -> Dict[str, Any]:
        derived = read_signal(tool, result)

        # A retrieved area only means something against the drop point, so the
        # comparison is made here rather than left for the model to attempt.
        if tool == "retrieve_location" and derived.get("has_last_known_point"):
            drop = facts.get("_drop")
            if drop:
                metres = _distance_m(
                    derived["last_known_lat"], derived["last_known_lon"], drop[0], drop[1]
                )
                derived["metres_from_drop"] = round(metres)
                derived["plausibly_at_the_door"] = metres <= LOBBY_TOLERANCE_M
        return derived

    def next_tool(
        self, case: Case, facts: Dict[str, Any], used: List[str]
    ) -> Optional[Tuple[str, Dict[str, Any], str]]:
        event = case.facts.get("event", "delivery_complete")
        if event == "shift_start":
            return self._shift_start(case, facts)
        if event == "rider_silent":
            return self._silent(case, facts)
        return self._delivery(case, facts)

    # -- shift start ---------------------------------------------------------

    def _shift_start(self, case: Case, facts: Dict[str, Any]):
        if "number_verified" not in facts:
            return (
                "verify_number",
                {},
                "Confirm the line on the phone before the shift opens. No code, "
                "no selfie, one unit.",
            )
        if not facts.get("number_verified"):
            return None

        gap = int(case.facts.get("hours_since_last_shift") or 24)
        if "sim_swapped_in_window" not in facts:
            return (
                "check_sim_swap",
                {"max_age_hours": gap},
                "Look back only as far as the last shift (%dh). A SIM change "
                "inside that gap is the account changing hands, not an upgrade "
                "from last year." % gap,
            )
        if facts.get("sim_swapped_in_window") and "device_swapped_in_window" not in facts:
            return (
                "check_device_swap",
                {"max_age_hours": gap},
                "The SIM changed since the last shift. If the handset changed "
                "too, this is a different person holding the account, and that "
                "is worth the second check.",
            )
        return None

    # -- delivery proof ------------------------------------------------------

    def _delivery(self, case: Case, facts: Dict[str, Any]):
        f = case.facts
        trusted = (
            float(f.get("trust_score") or 0) >= TRUSTED_SCORE
            and int(f.get("deliveries_completed") or 0) >= TRUSTED_DELIVERIES
            and not f.get("customer_disputed")
        )
        if trusted:
            # The whole product depends on this branch being reachable.
            return None

        if "location_result" not in facts:
            return (
                "verify_location",
                {},
                "Ask the network a yes/no question about the drop point. Two "
                "units, no coordinates returned, and an app on the rider's "
                "phone cannot fake the answer.",
            )

        if facts.get("location_uncertain") and "metres_from_drop" not in facts:
            return (
                "retrieve_location",
                {},
                "The network answered %s, which means the line's uncertainty "
                "circle straddles the drop point - exactly what a tower lobby "
                "looks like. Buy the area before contradicting the rider."
                % facts.get("location_result"),
            )

        if (
            facts.get("location_outside")
            and f.get("customer_disputed")
            and "device_swapped_in_window" not in facts
        ):
            return (
                "check_device_swap",
                {"max_age_hours": 24},
                "The drop does not verify and the customer is disputing. Check "
                "whether the handset changed today, which would point at a "
                "rented account rather than a mistaken rider.",
            )
        return None

    # -- silent rider --------------------------------------------------------

    def _silent(self, case: Case, facts: Dict[str, Any]):
        if "reachability" not in facts:
            return (
                "check_reachability",
                {},
                "One unit decides everything here: a dead phone and a rider on "
                "the ground look the same on a dispatch board.",
            )
        if facts.get("reachable"):
            return None

        if "has_last_known_point" not in facts:
            return (
                "retrieve_location",
                {},
                "The line is not reachable. Support needs somewhere to start, so "
                "this is the moment the more revealing call is justified.",
            )
        if "congestion" not in facts:
            return (
                "query_congestion",
                {},
                "Find out whether the cell is saturated before promising support "
                "a video call that will not connect.",
            )
        if facts.get("cell_crowded") and "qod_session_id" not in facts:
            return (
                "reserve_quality",
                {"profile": "QOS_L", "duration_s": 600},
                "The cell is busy and support is about to open a video call to "
                "the last known area. Reserve the quality rather than hope for it.",
            )
        return None

    # -- the floor -----------------------------------------------------------

    def decide(self, case: Case, facts: Dict[str, Any]) -> Tuple[str, str, str, float]:
        event = case.facts.get("event", "delivery_complete")

        if event == "shift_start":
            return self._decide_shift(case, facts)
        if event == "rider_silent":
            return self._decide_silent(case, facts)
        return self._decide_delivery(case, facts)

    def _decide_shift(self, case: Case, facts: Dict[str, Any]):
        if "number_verified" not in facts:
            return (
                "challenge",
                "Hold the shift and verify this rider through the depot",
                "No network evidence was available for this line, so the shift "
                "cannot be opened on network proof alone.",
                0.4,
            )
        if not facts.get("number_verified"):
            return (
                "suspend",
                "Do not open the shift; send the rider to the depot with ID",
                "The network will not confirm that this line belongs to the phone "
                "asking to start the shift.",
                0.95,
            )
        if facts.get("sim_swapped_in_window") and facts.get("device_swapped_in_window"):
            return (
                "suspend",
                "Suspend the account now and call the rider on the number on file",
                "Both the SIM and the handset behind this account changed since "
                "the last shift. That is an account that changed hands, not a "
                "rider who bought a phone.",
                0.93,
            )
        if facts.get("sim_swapped_in_window"):
            return (
                "challenge",
                "Ask for a depot check-in before the first pickup",
                "The SIM changed since the last shift but the handset did not. "
                "Most likely a replacement SIM, worth one human confirmation "
                "before the rider carries anyone's food.",
                0.84,
            )
        return (
            "verify",
            "Open the shift",
            "The network confirms the approved line is on the phone starting this "
            "shift, and nothing behind the account changed since the last one.",
            0.9,
        )

    def _decide_delivery(self, case: Case, facts: Dict[str, Any]):
        f = case.facts
        # ``facts`` starts as a copy of the case, so emptiness is not the test.
        # What matters is whether any network answer came back at all.
        network_answered = any(
            key in facts
            for key in ("location_result", "device_swapped_in_window", "metres_from_drop")
        )
        if not network_answered:
            return (
                "accept",
                "Accept the proof of delivery",
                "This rider has %s completed deliveries and a trust score of %s "
                "with no dispute on this order, so no network check was bought. "
                "Spending here would have changed nothing."
                % (f.get("deliveries_completed"), f.get("trust_score")),
                0.86,
            )

        if facts.get("location_inside"):
            return (
                "verify",
                "Accept the proof; it is network-backed",
                "The network confirms the rider's line was inside the drop circle "
                "when the delivery was marked complete. This answer does not come "
                "from the rider's phone, so it cannot be spoofed by an app.",
                0.94,
            )

        if facts.get("location_uncertain"):
            metres = facts.get("metres_from_drop")
            if facts.get("plausibly_at_the_door"):
                return (
                    "verify",
                    "Accept the proof and record why the answer was uncertain",
                    "The network could not give a clean yes or no, which is what a "
                    "lobby or a basement looks like. The retrieved area puts the "
                    "line %sm from the door, so the rider was there and the "
                    "uncertainty is the building's, not the rider's." % metres,
                    0.78,
                )
            if metres is not None:
                return (
                    "challenge",
                    "Hold the proof and ask the rider for a photo at the door",
                    "The area check was uncertain, so a position was retrieved: it "
                    "puts the line %sm from the drop point, too far to be the same "
                    "building. Worth asking about, not yet worth an accusation." % metres,
                    0.8,
                )
            return (
                "verify",
                "Accept the proof; the network could not answer either way",
                "The network answered %s and no position could be retrieved. There "
                "is no evidence against this rider, so the benefit of the doubt is "
                "the only fair outcome." % facts.get("location_result"),
                0.55,
            )

        if facts.get("location_outside"):
            if facts.get("device_swapped_in_window"):
                return (
                    "suspend",
                    "Suspend the account and open a fraud case",
                    "The drop does not verify, the customer is disputing, and the "
                    "handset behind this account changed today. That combination is "
                    "a rented account, not a lost rider.",
                    0.91,
                )
            return (
                "challenge",
                "Hold the proof, pay nothing yet, and ask the rider what happened",
                "The network places this line outside the drop circle at the moment "
                "the delivery was marked complete. The rider may have a reason; the "
                "proof does not stand on its own.",
                0.88,
            )

        return (
            "verify",
            "Accept the proof",
            "Nothing the network returned contradicts the delivery.",
            0.7,
        )

    def _decide_silent(self, case: Case, facts: Dict[str, Any]):
        minutes = case.facts.get("silent_minutes", "several")
        if "reachability" not in facts:
            return (
                "challenge",
                "Call the rider on the number on file",
                "No network evidence was available, so support has nothing beyond "
                "the silence itself.",
                0.4,
            )
        if facts.get("reachable_on_data"):
            return (
                "challenge",
                "Send an in-app prompt; the phone is fine",
                "The line is reachable on data, so this is not a dead battery and "
                "not a crash that took the phone out. The rider is not answering, "
                "which is a conversation, not an emergency.",
                0.85,
            )
        if facts.get("reachable"):
            return (
                "challenge",
                "Send an SMS; the line is reachable but not on data",
                "The line answers on SMS but not data, which usually means poor "
                "coverage rather than an incident.",
                0.8,
            )
        detail = ""
        if facts.get("has_last_known_point"):
            detail = (
                " Last known area retrieved for the response team%s."
                % (", on a reserved quality session" if facts.get("qod_session_id") else "")
            )
        return (
            "incident",
            "Treat as a possible incident: dispatch a welfare check to the last known area",
            "The line has been silent for %s minutes and the network cannot reach "
            "it at all. That rules out a rider ignoring the app and is the pattern "
            "of a phone that lost power in a fall.%s" % (minutes, detail),
            0.87,
        )


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * r * asin(sqrt(a))
