"""Transport and provenance tests for the CAMARA client."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from core.camara import CamaraClient, ConsentError, Device
from core.config import API_PREFIX, NacConfig
from core.consent import ConsentLedger
from core.simulator import LineProfile, NetworkSimulator


@pytest.fixture
def sim() -> NetworkSimulator:
    return NetworkSimulator(
        [
            LineProfile(
                msisdn="+905551112233",
                sim_swap_hours_ago=6,
                device_swap_hours_ago=200,
                latitude=41.0,
                longitude=29.0,
                location_accuracy_m=100,
            )
        ]
    )


@pytest.fixture
def client(sim: NetworkSimulator) -> CamaraClient:
    return CamaraClient(NacConfig(mode="simulator"), simulator=sim)


DEVICE = Device("+905551112233")


def test_device_serialises_to_camara_shape():
    device = Device("+905551112233", network_access_id="rider@carrier", ipv4="10.1.2.3")
    body = device.to_camara()
    assert body["phoneNumber"] == "+905551112233"
    assert body["networkAccessIdentifier"] == "rider@carrier"
    assert body["ipv4Address"]["publicAddress"] == "10.1.2.3"
    # An unset identifier must be absent, not null: CAMARA rejects nulls here.
    assert "ipv6Address" not in body


def test_every_result_carries_provenance(client: CamaraClient):
    result = client.number_verification_verify(DEVICE)
    assert result.api == "number-verification"
    assert result.operation == "verify"
    assert result.source == "simulator"
    assert result.request_digest, "a request digest is what makes a call auditable"
    assert result.at.endswith("+00:00") or result.at.endswith("Z")
    assert result.latency_ms >= 0


def test_endpoints_include_the_gateway_prefix(client: CamaraClient):
    assert client.sim_swap_check(DEVICE).endpoint == API_PREFIX["sim-swap"] + "/check"
    assert client.device_swap_check(DEVICE).endpoint == API_PREFIX["device-swap"] + "/check"
    assert (
        client.number_verification_verify(DEVICE).endpoint
        == API_PREFIX["number-verification"] + "/verify"
    )
    # Nokia mounts these at the host root, so the prefix is empty by design.
    assert client.location_verify(DEVICE, 41.0, 29.0, 500).endpoint == "/verify"


def test_call_log_accumulates_in_order(client: CamaraClient):
    client.number_verification_verify(DEVICE)
    client.sim_swap_check(DEVICE)
    client.device_reachability(DEVICE)
    assert [c.api for c in client.call_log] == [
        "number-verification",
        "sim-swap",
        "device-status",
    ]
    assert client.describe()["calls_made"] == 3


def test_consent_gate_refuses_ungranted_scopes(sim: NetworkSimulator):
    ledger = ConsentLedger()
    guarded = CamaraClient(NacConfig(mode="simulator"), simulator=sim, consent=ledger)

    with pytest.raises(ConsentError):
        guarded.sim_swap_check(DEVICE)

    ledger.grant("+905551112233", ["fraud:sim-swap"], duration=timedelta(minutes=5))
    assert guarded.sim_swap_check(DEVICE).data == {"swapped": True}

    # A different scope is still refused: one grant is not a blank cheque.
    with pytest.raises(ConsentError):
        guarded.location_retrieve(DEVICE)


def test_revoked_consent_stops_further_calls(sim: NetworkSimulator):
    ledger = ConsentLedger()
    ledger.grant("+905551112233", ["device:status"], duration=timedelta(minutes=5))
    guarded = CamaraClient(NacConfig(mode="simulator"), simulator=sim, consent=ledger)
    assert guarded.device_reachability(DEVICE).status == 200

    ledger.revoke("+905551112233")
    with pytest.raises(ConsentError):
        guarded.device_reachability(DEVICE)


def test_simulator_mode_never_claims_to_be_live(client: CamaraClient):
    for result in (
        client.number_verification_verify(DEVICE),
        client.congestion_query(DEVICE),
        client.qod_create_session(DEVICE, "QOS_L", 300, "10.0.0.1"),
    ):
        assert result.source == "simulator"


def test_live_mode_without_credentials_falls_back_to_simulator():
    config = NacConfig(mode="live", rapid_key="")
    client = CamaraClient(config, simulator=NetworkSimulator())
    assert client.describe()["effective_source"] == "simulator"


def test_hybrid_http_failure_is_truthfully_tagged_and_uses_the_operation(sim):
    """A failed live geofence call must not become fake ``live`` evidence."""

    client = CamaraClient(
        NacConfig(mode="hybrid", rapid_key="test-key"), simulator=sim
    )
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"error": "maintenance"})
        )
    )
    try:
        result = client.geofencing_subscribe(
            DEVICE,
            41.0,
            29.0,
            500,
            "https://example.invalid/geofence",
        )
    finally:
        client.close()

    assert result.source == "simulator"
    assert result.data["status"] == "ACTIVE"
    assert "simulated" not in result.data
    assert client.describe()["effective_source"] == "hybrid"


def test_successful_hybrid_request_keeps_live_provenance(sim):
    client = CamaraClient(
        NacConfig(mode="hybrid", rapid_key="test-key"), simulator=sim
    )
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"devicePhoneNumberVerified": True}
            )
        )
    )
    try:
        result = client.number_verification_verify(DEVICE)
    finally:
        client.close()

    assert result.source == "live"
    assert result.data == {"devicePhoneNumberVerified": True}


def test_qod_and_slice_calls_are_the_expensive_ones(client: CamaraClient):
    cheap = client.device_reachability(DEVICE)
    pricey = client.qod_create_session(DEVICE, "QOS_L", 300, "10.0.0.1")
    assert pricey.cost_units > cheap.cost_units
