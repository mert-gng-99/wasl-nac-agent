"""The simulator has to be boring and predictable, or no demo can be rehearsed."""

from __future__ import annotations

from core.simulator import (
    CONGESTION_HIGH,
    NOT_REACHABLE,
    VERIFY_FALSE,
    VERIFY_PARTIAL,
    VERIFY_TRUE,
    VERIFY_UNKNOWN,
    LineProfile,
    NetworkSimulator,
)

AREA = {"areaType": "CIRCLE", "center": {"latitude": 41.0, "longitude": 29.0}, "radius": 500}


def test_unregistered_lines_are_stable_across_instances():
    first = NetworkSimulator().profile_for("+905559998877")
    second = NetworkSimulator().profile_for("+905559998877")
    assert first.reachability == second.reachability
    assert first.roaming == second.roaming
    assert first.congestion == second.congestion


def test_msisdn_normalisation_finds_the_same_line():
    sim = NetworkSimulator([LineProfile(msisdn="+905551112233", label="registered")])
    for spelling in ("+905551112233", "905551112233", "+90 555 111 2233", "+90-555-111-2233"):
        assert sim.profile_for(spelling).label == "registered"


def test_sim_swap_window_is_respected():
    sim = NetworkSimulator([LineProfile(msisdn="+900000000001", sim_swap_hours_ago=100)])
    body = {"phoneNumber": "+900000000001", "maxAge": 240}
    assert sim.respond("sim-swap", "check", body)["swapped"] is True
    body["maxAge"] = 48
    assert sim.respond("sim-swap", "check", body)["swapped"] is False


def test_sim_swap_date_is_null_when_no_record_exists():
    sim = NetworkSimulator([LineProfile(msisdn="+900000000002")])
    answer = sim.respond("sim-swap", "retrieve-date", {"phoneNumber": "+900000000002"})
    assert answer["latestSimChange"] is None


def test_location_verification_returns_all_four_camara_answers():
    inside = NetworkSimulator(
        [LineProfile(msisdn="+901", latitude=41.0, longitude=29.0, location_accuracy_m=50)]
    )
    assert (
        inside.respond("location-verification", "verify", {"phoneNumber": "+901", "area": AREA})[
            "verificationResult"
        ]
        == VERIFY_TRUE
    )

    far = NetworkSimulator(
        [LineProfile(msisdn="+902", latitude=41.5, longitude=29.0, location_accuracy_m=50)]
    )
    assert (
        far.respond("location-verification", "verify", {"phoneNumber": "+902", "area": AREA})[
            "verificationResult"
        ]
        == VERIFY_FALSE
    )

    # A big uncertainty circle straddling the boundary is PARTIAL, not a guess.
    straddling = NetworkSimulator(
        [LineProfile(msisdn="+903", latitude=41.004, longitude=29.0, location_accuracy_m=800)]
    )
    assert (
        straddling.respond(
            "location-verification", "verify", {"phoneNumber": "+903", "area": AREA}
        )["verificationResult"]
        == VERIFY_PARTIAL
    )

    # No location at all is UNKNOWN, which is a real answer the network gives.
    nowhere = NetworkSimulator([LineProfile(msisdn="+904")])
    assert (
        nowhere.respond("location-verification", "verify", {"phoneNumber": "+904", "area": AREA})[
            "verificationResult"
        ]
        == VERIFY_UNKNOWN
    )


def test_forced_verification_overrides_geometry():
    sim = NetworkSimulator(
        [
            LineProfile(
                msisdn="+905",
                latitude=41.0,
                longitude=29.0,
                location_accuracy_m=10,
                force_verification=VERIFY_PARTIAL,
            )
        ]
    )
    answer = sim.respond("location-verification", "verify", {"phoneNumber": "+905", "area": AREA})
    assert answer["verificationResult"] == VERIFY_PARTIAL


def test_congestion_comes_back_in_the_wrapped_list_shape():
    sim = NetworkSimulator([LineProfile(msisdn="+906", congestion=CONGESTION_HIGH)])
    answer = sim.respond("congestion", "query", {"phoneNumber": "+906"})
    assert answer["result"][0]["congestionLevel"] == CONGESTION_HIGH
    assert "timeIntervalStart" in answer["result"][0]


def test_update_changes_a_line_mid_demo():
    sim = NetworkSimulator([LineProfile(msisdn="+907")])
    assert sim.respond("device-status", "connectivity", {"phoneNumber": "+907"})[
        "connectivityStatus"
    ] != NOT_REACHABLE
    sim.update("+907", reachability=NOT_REACHABLE)
    assert (
        sim.respond("device-status", "connectivity", {"phoneNumber": "+907"})["connectivityStatus"]
        == NOT_REACHABLE
    )


def test_qod_and_geofence_return_identifiers_worth_storing():
    sim = NetworkSimulator([LineProfile(msisdn="+908")])
    session = sim.respond(
        "qod", "create-session", {"phoneNumber": "+908", "qosProfile": "QOS_L", "duration": 300}
    )
    assert session["sessionId"] and session["qosStatus"] == "AVAILABLE"

    subscription = sim.respond(
        "geofencing",
        "subscribe",
        {"types": ["org.camara.geofencing.v0.area-left"], "sink": "https://x.invalid"},
    )
    assert subscription["id"] and subscription["status"] == "ACTIVE"
