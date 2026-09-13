"""Wasl product spec: theme 5, industrial and enterprise AI automation."""

from __future__ import annotations

from core.agent import Case
from core.idea import ConsentPlan, IdeaSpec, LevelStyle, Scenario, UiSpec
from core.simulator import LineProfile

from .policy import WaslPolicy

POLICY = WaslPolicy()

# A real drop point in Business Bay, Dubai, and a tight 150 m circle - a
# delivery either happened at the door or it did not.
DROP = (25.1857, 55.2766)
DROP_RADIUS_M = 150

# Metres to degrees at this latitude, close enough for a demo geometry.
_M_PER_DEG_LAT = 111320.0


def _north_of_drop(metres: float) -> tuple:
    return (DROP[0] + metres / _M_PER_DEG_LAT, DROP[1])


LINE_SHIFT_CLEAN = LineProfile(
    msisdn="+971500000201",
    label="Rider starting a shift, nothing changed since yesterday",
    notes="The ordinary case. Two calls and the shift opens.",
)

LINE_SHIFT_HANDOVER = LineProfile(
    msisdn="+971500000202",
    label="SIM and handset both changed since the last shift",
    sim_swap_hours_ago=5,
    device_swap_hours_ago=4,
    notes="An account rented out for the day.",
)

LINE_TRUSTED = LineProfile(
    msisdn="+971500000203",
    label="2,100 clean deliveries",
    latitude=DROP[0],
    longitude=DROP[1],
    location_accuracy_m=60,
    notes="Gets no network check at all, which is the point.",
)

LINE_DISPUTED = LineProfile(
    msisdn="+971500000204",
    label="Delivery marked complete 8 km from the door, customer disputing",
    latitude=_north_of_drop(8000)[0],
    longitude=_north_of_drop(8000)[1],
    location_accuracy_m=120,
    device_swap_hours_ago=6,
    notes="Spoofed location plus a handset swapped this morning.",
)

LINE_LOBBY = LineProfile(
    msisdn="+971500000205",
    label="Rider in a tower lobby, network answers PARTIAL",
    latitude=_north_of_drop(250)[0],
    longitude=_north_of_drop(250)[1],
    location_accuracy_m=300,
    force_verification="PARTIAL",
    notes="The answer most demos pretend does not exist.",
)

LINE_WANDERED = LineProfile(
    msisdn="+971500000207",
    label="Delivery marked complete 1.2 km from the door, no dispute",
    latitude=_north_of_drop(1200)[0],
    longitude=_north_of_drop(1200)[1],
    location_accuracy_m=120,
    notes="Probably a rider who tapped early, not a fraudster.",
)

LINE_SILENT = LineProfile(
    msisdn="+971500000206",
    label="Silent for 14 minutes mid-shift, cell saturated",
    reachability="NOT_CONNECTED",
    latitude=_north_of_drop(900)[0],
    longitude=_north_of_drop(900)[1],
    location_accuracy_m=400,
    congestion="high",
    notes="A dead phone and a rider on the ground look identical until you ask.",
)


def _shift(subject: str, name: str, gap_hours: int, label: str) -> Case:
    return Case(
        subject=subject,
        kind="rider_event",
        label=label,
        facts={
            "event": "shift_start",
            "rider_name": name,
            "hours_since_last_shift": gap_hours,
            "deliveries_completed": 640,
            "trust_score": 0.88,
        },
    )


def _delivery(subject: str, name: str, label: str, **facts) -> Case:
    base = {
        "event": "delivery_complete",
        "rider_name": name,
        "order_value": 120,
        "customer_disputed": False,
        "deliveries_completed": 310,
        "trust_score": 0.74,
        # The policy compares any retrieved area against this, so it travels
        # with the case rather than being inferred.
        "_drop": DROP,
    }
    base.update(facts)
    return Case(
        subject=subject,
        kind="rider_event",
        label=label,
        facts=base,
        latitude=DROP[0],
        longitude=DROP[1],
        radius_m=DROP_RADIUS_M,
    )


SCENARIOS = [
    Scenario(
        id="shift-start-clean",
        title="Rider opens a shift",
        subtitle="Nothing behind the account changed since yesterday",
        expect_level="verify",
        lines=[LINE_SHIFT_CLEAN],
        narrative="Identity at shift start, with no code and no selfie.",
        teaches=(
            "The SIM swap window is set to the gap since the last shift, not to a "
            "fixed 240 hours. A change last year is irrelevant; a change in the "
            "last fourteen hours is the whole question."
        ),
        build_case=lambda: _shift(
            LINE_SHIFT_CLEAN.msisdn, "Imran", 14, "Shift start, clean account"
        ),
    ),
    Scenario(
        id="shift-start-handover",
        title="The account changed hands overnight",
        subtitle="Both the SIM and the handset changed since the last shift",
        expect_level="suspend",
        lines=[LINE_SHIFT_HANDOVER],
        narrative="The rented account this product exists to catch.",
        teaches=(
            "One change is a rider who bought a phone. Both changes, inside the "
            "gap between two shifts, is a different person holding the account."
        ),
        build_case=lambda: _shift(
            LINE_SHIFT_HANDOVER.msisdn, "Account 4471", 14, "Shift start, account handed over"
        ),
    ),
    Scenario(
        id="delivery-trusted",
        title="A trusted rider marks a delivery complete",
        subtitle="2,100 deliveries, trust score 0.96, no dispute",
        expect_level="accept",
        lines=[LINE_TRUSTED],
        narrative="The branch that makes the unit economics work.",
        teaches=(
            "Zero CAMARA calls. The agent decides this proof is not worth "
            "verifying, and says so in the record. A platform that checks every "
            "delivery cannot afford this product."
        ),
        build_case=lambda: _delivery(
            LINE_TRUSTED.msisdn,
            "Rashid",
            "Trusted rider, routine drop",
            deliveries_completed=2100,
            trust_score=0.96,
        ),
    ),
    Scenario(
        id="delivery-verified",
        title="A newer rider marks a delivery complete",
        subtitle="310 deliveries, trust score 0.74, drop verified at the door",
        expect_level="verify",
        lines=[
            LineProfile(
                msisdn="+971500000208",
                label="Newer rider, genuinely at the door",
                latitude=DROP[0],
                longitude=DROP[1],
                location_accuracy_m=60,
                notes="One area check, answer TRUE, proof stands.",
            )
        ],
        narrative="Proof that came from the network rather than the rider's phone.",
        teaches=(
            "One yes/no area question, two units, no coordinates returned. An app "
            "on the rider's handset cannot change this answer."
        ),
        build_case=lambda: _delivery(
            "+971500000208", "Bilal", "Newer rider, drop verified"
        ),
    ),
    Scenario(
        id="delivery-lobby-partial",
        title="The network answers PARTIAL",
        subtitle="Rider in a tower lobby; the uncertainty circle straddles the door",
        expect_level="verify",
        lines=[LINE_LOBBY],
        narrative="The answer that gets honest riders accused.",
        teaches=(
            "PARTIAL is a real CAMARA answer, not a yes and not a no. The agent "
            "buys the retrieved area, finds the line 250 m from the door, and "
            "accepts the delivery. Rounding PARTIAL to FALSE is how a platform "
            "docks pay from people who did their job."
        ),
        build_case=lambda: _delivery(
            LINE_LOBBY.msisdn, "Yusuf", "Drop in a tower lobby"
        ),
    ),
    Scenario(
        id="delivery-far-off",
        title="Marked complete 1.2 km from the door",
        subtitle="No customer dispute, no device change",
        expect_level="challenge",
        lines=[LINE_WANDERED],
        narrative="Probably a rider who tapped early.",
        teaches=(
            "The network contradicts the proof, so the delivery is held and the "
            "rider is asked. It is not escalated to fraud, because nothing else "
            "supports that reading."
        ),
        build_case=lambda: _delivery(
            LINE_WANDERED.msisdn, "Tariq", "Marked complete too early"
        ),
    ),
    Scenario(
        id="delivery-rented-account",
        title="Disputed delivery from a swapped handset",
        subtitle="8 km from the door, customer disputing, handset changed this morning",
        expect_level="suspend",
        lines=[LINE_DISPUTED],
        narrative="Three signals pointing the same way.",
        teaches=(
            "Only now does the agent buy a device swap check, because the location "
            "answer and the customer dispute together make it worth three units. "
            "Escalation is earned by evidence, not triggered by a keyword."
        ),
        build_case=lambda: _delivery(
            LINE_DISPUTED.msisdn,
            "Account 8820",
            "Disputed delivery, spoofed location",
            customer_disputed=True,
            order_value=430,
        ),
    ),
    Scenario(
        id="rider-silent",
        title="A rider goes quiet",
        subtitle="14 minutes of silence, and the cell around the last ping is saturated",
        expect_level="incident",
        lines=[LINE_SILENT],
        narrative="The case where the network answer changes what support does.",
        teaches=(
            "Reachability first, for one unit: the line is not reachable at all, "
            "which rules out a rider ignoring the app. Then the last known area, "
            "then congestion, and because the cell is saturated the agent reserves "
            "quality so the welfare call actually connects."
        ),
        build_case=lambda: Case(
            subject=LINE_SILENT.msisdn,
            kind="rider_event",
            label="Rider silent mid-shift",
            facts={
                "event": "rider_silent",
                "rider_name": "Imran",
                "silent_minutes": 14,
                "deliveries_completed": 640,
                "trust_score": 0.88,
            },
            latitude=DROP[0],
            longitude=DROP[1],
            radius_m=2000,
            params={"qos_profile": "QOS_L", "qos_duration_s": 600},
        ),
    ),
]

SPEC = IdeaSpec(
    slug="wasl",
    name="Wasl",
    tagline="Network-backed proof of who is riding and what was delivered",
    theme_number=5,
    theme_name="Industrial & Enterprise AI Automation",
    submission_title="Wasl - network backed proof of who is riding and what was delivered",
    submission_description=(
        "Wasl is an AI agent that moves two unverifiable proofs in delivery "
        "logistics onto the mobile network: that the approved rider is the "
        "person on the bike, and that the delivery happened where it was marked. "
        "It spends network checks only on the riders it has a reason to check."
    ),
    policy=POLICY,
    scenarios=SCENARIOS,
    lines=[
        LINE_SHIFT_CLEAN,
        LINE_SHIFT_HANDOVER,
        LINE_TRUSTED,
        LINE_DISPUTED,
        LINE_LOBBY,
        LINE_WANDERED,
        LINE_SILENT,
    ],
    consent=ConsentPlan(
        moment="at induction, as one clear line in the rider contract pack",
        scopes=[
            "identity:verify",
            "fraud:sim-swap",
            "fraud:device-swap",
            "device:status",
            "location:verify",
            "location:retrieve",
            "network:insights",
            "network:qod",
        ],
        who_consents="the rider, who is an enrolled worker and the owner of the line on file",
        duration_note=(
            "Checks run during a booked shift and during an open incident, never "
            "outside one. No track of the rider's day is ever built."
        ),
        revocation=(
            "A rider can withdraw at any time; the platform then falls back to its "
            "existing app-based proof for that rider."
        ),
    ),
    ui=UiSpec(
        accent="#2f6fd0",
        accent_soft="#e7effc",
        hero_kicker=(
            "Accounts get rented for the day and app locations can be faked. Wasl "
            "asks the network instead, and only about the riders worth asking about."
        ),
        subject_label="Rider line (MSISDN)",
        case_label="Rider event",
        run_all_label="Run all eight rider events",
        ad_hoc_placeholder="+971500000204",
        ad_hoc_help=(
            "An ad-hoc check runs the delivery-proof path against the Business Bay "
            "drop point. Unregistered numbers get a stable derived profile."
        ),
        levels=[
            LevelStyle("accept", "Accept - no check needed", "calm",
                       "A trusted rider's proof taken at their word."),
            LevelStyle("verify", "Verified by the network", "calm",
                       "Confirmed by an answer the rider's phone cannot change."),
            LevelStyle("challenge", "Challenge - hold and ask", "watch",
                       "The proof does not stand on its own."),
            LevelStyle("incident", "Possible incident - dispatch", "warn",
                       "The line cannot be reached at all."),
            LevelStyle("suspend", "Suspend the account", "alarm",
                       "The account is not being used by the approved rider."),
        ],
    ),
    honest_limits=[
        "Wasl proves which line was at the door. It does not prove which human "
        "held the phone, so a rider who hands their own phone to a friend is "
        "invisible to it.",
        "Location verification answers at cell and area resolution. A 150 m drop "
        "circle works in a dense city; a rural drop needs a wider circle and a "
        "weaker claim.",
        "PARTIAL answers are common in basements and towers, and the agent is "
        "deliberately generous with them. That means some genuinely false proofs "
        "will be accepted rather than risk docking honest riders' pay.",
        "Nothing here detects food quality, theft in transit, or a rider who "
        "delivers to the wrong flat in the right building.",
    ],
    buyers=[
        "Delivery platforms and courier firms paying a monthly price per active rider",
        "Logistics fleets who need proof of handover for B2B parcels",
        "Mobile operators, who share the API income and can sell Wasl with the fleet SIM plan",
    ],
    repo_name="wasl-nac-agent",
    demo_notes=(
        "Run the trusted rider and the tower lobby scenarios back to back: one "
        "buys nothing, the other refuses to accuse on an uncertain answer."
    ),
)
