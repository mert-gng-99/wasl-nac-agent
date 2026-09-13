"""CAMARA client for the Nokia Network-as-Code platform.

One class, ``CamaraClient``, exposes the eleven CAMARA API families available
on the Network-as-Code portal. Two things make it different from a thin
requests wrapper:

1.  Every call returns an :class:`ApiResult` carrying provenance - which API,
    which endpoint, how long it took, whether the answer came from the live
    network or the simulator, and a digest of the request. The agent's evidence
    trail is built out of these, so a decision can always be traced back to the
    exact network answers behind it.

2.  Calls route through a consent gate. CAMARA location and identity APIs are
    only lawful with the consent of the line owner, so an ungranted call is
    refused here rather than left to the caller to remember.

Request and response shapes follow the CAMARA specs as published on the Nokia
gateway: ``POST /check`` for SIM swap, ``POST /verify`` for location
verification, ``POST /connectivity`` and ``POST /roaming`` for device status,
and so on.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import API_PREFIX, NacConfig
from .simulator import NetworkSimulator


class ConsentError(RuntimeError):
    """Raised when a CAMARA call is attempted outside a granted consent scope."""


class CamaraError(RuntimeError):
    """A live CAMARA endpoint returned an error we cannot use."""

    def __init__(self, message: str, *, api: str, status: int, body: Any = None):
        super().__init__(message)
        self.api = api
        self.status = status
        self.body = body


@dataclass(frozen=True)
class Device:
    """CAMARA device identifier.

    A phone number is enough for every API used here. The other identifier
    forms are kept because network access id is what some operators expose for
    enterprise SIMs.
    """

    phone_number: str
    network_access_id: Optional[str] = None
    ipv4: Optional[str] = None
    ipv6: Optional[str] = None

    def to_camara(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if self.phone_number:
            body["phoneNumber"] = self.phone_number
        if self.network_access_id:
            body["networkAccessIdentifier"] = self.network_access_id
        if self.ipv4:
            body["ipv4Address"] = {"publicAddress": self.ipv4, "privateAddress": self.ipv4}
        if self.ipv6:
            body["ipv6Address"] = self.ipv6
        return body

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.phone_number


@dataclass
class ApiResult:
    """A single network answer plus everything needed to audit it later."""

    api: str
    operation: str
    endpoint: str
    data: Dict[str, Any]
    source: str                      # "live" | "simulator"
    latency_ms: int
    status: int = 200
    request_digest: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cost_units: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class CamaraClient:
    """Calls CAMARA APIs on the Nokia Network-as-Code gateway.

    ``consent`` is any object with ``allows(scope, subject) -> bool``. Passing
    ``None`` disables the gate and is only meant for unit tests of the
    transport itself.
    """

    def __init__(
        self,
        config: NacConfig,
        *,
        simulator: Optional[NetworkSimulator] = None,
        consent: Any = None,
    ) -> None:
        self.config = config
        self.simulator = simulator or NetworkSimulator()
        self.consent = consent
        self._client: Optional[httpx.Client] = None
        self.call_log: List[ApiResult] = []

    # -- transport -----------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout_s)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _use_live(self) -> bool:
        return self.config.mode in {"live", "hybrid"} and self.config.has_credentials

    def _check_consent(self, scope: str, subject: Optional[str]) -> None:
        if self.consent is None or subject is None:
            return
        if not self.consent.allows(scope, subject):
            raise ConsentError(
                "no active consent for scope '%s' on subject '%s'" % (scope, subject)
            )

    def _call(
        self,
        api: str,
        operation: str,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        scope: str = "",
        subject: Optional[str] = None,
        cost_units: float = 0.0,
    ) -> ApiResult:
        if scope:
            self._check_consent(scope, subject)

        started = time.perf_counter()
        endpoint = API_PREFIX.get(api, "") + path

        if self._use_live():
            data, status, source = self._call_live(
                api, operation, method, endpoint, body, subject
            )
        else:
            data = self.simulator.respond(api, operation, body or {}, subject=subject)
            status = 200
            source = "simulator"

        result = ApiResult(
            api=api,
            operation=operation,
            endpoint=endpoint,
            data=data,
            source=source,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status=status,
            request_digest=_digest(body or {}),
            cost_units=cost_units,
        )
        self.call_log.append(result)
        return result

    def _call_live(
        self,
        api: str,
        operation: str,
        method: str,
        endpoint: str,
        body: Optional[Dict[str, Any]],
        subject: Optional[str],
    ) -> Tuple[Dict[str, Any], int, str]:
        url = self.config.base_url(api) + endpoint
        headers = {
            "content-type": "application/json",
            "X-RapidAPI-Key": self.config.rapid_key,
            "X-RapidAPI-Host": self.config.host(api),
        }
        try:
            response = self._http().request(method, url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            if self.config.mode == "hybrid":
                return (
                    self.simulator.respond(api, operation, body or {}, subject=subject),
                    200,
                    "simulator",
                )
            raise CamaraError(
                "%s: transport failure: %s" % (api, exc), api=api, status=0
            ) from exc

        if response.status_code >= 400:
            detail: Any
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:400]
            if self.config.mode == "hybrid":
                return (
                    self.simulator.respond(api, operation, body or {}, subject=subject),
                    200,
                    "simulator",
                )
            raise CamaraError(
                "%s returned %s" % (api, response.status_code),
                api=api,
                status=response.status_code,
                body=detail,
            )

        if not response.content:
            return {}, response.status_code, "live"
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:800]}
        if not isinstance(payload, dict):
            payload = {"result": payload}
        return payload, response.status_code, "live"

    # -- Digital identity and anti-fraud ------------------------------------

    def number_verification_verify(self, device: Device) -> ApiResult:
        """CAMARA number-verification: does this line really belong to this phone?

        Answers without an SMS code, because the answer comes from the network
        session rather than from something the user can be tricked into typing.
        """
        return self._call(
            "number-verification",
            "verify",
            "POST",
            "/verify",
            body={"phoneNumber": device.phone_number},
            scope="identity:verify",
            subject=device.phone_number,
            cost_units=1.0,
        )

    def sim_swap_check(self, device: Device, max_age_hours: int = 240) -> ApiResult:
        """CAMARA sim-swap: has the SIM for this line changed inside max_age?"""
        return self._call(
            "sim-swap",
            "check",
            "POST",
            "/check",
            body={"phoneNumber": device.phone_number, "maxAge": max_age_hours},
            scope="fraud:sim-swap",
            subject=device.phone_number,
            cost_units=3.0,
        )

    def sim_swap_date(self, device: Device) -> ApiResult:
        """CAMARA sim-swap: timestamp of the most recent SIM change."""
        return self._call(
            "sim-swap",
            "retrieve-date",
            "POST",
            "/retrieve-date",
            body={"phoneNumber": device.phone_number},
            scope="fraud:sim-swap",
            subject=device.phone_number,
            cost_units=3.0,
        )

    def device_swap_check(self, device: Device, max_age_hours: int = 240) -> ApiResult:
        """CAMARA device-swap: has the handset behind this line changed?"""
        return self._call(
            "device-swap",
            "check",
            "POST",
            "/check",
            body={"phoneNumber": device.phone_number, "maxAge": max_age_hours},
            scope="fraud:device-swap",
            subject=device.phone_number,
            cost_units=3.0,
        )

    def device_swap_date(self, device: Device) -> ApiResult:
        return self._call(
            "device-swap",
            "retrieve-date",
            "POST",
            "/retrieve-date",
            body={"phoneNumber": device.phone_number},
            scope="fraud:device-swap",
            subject=device.phone_number,
            cost_units=3.0,
        )

    # -- Device intelligence -------------------------------------------------

    def location_verify(
        self,
        device: Device,
        latitude: float,
        longitude: float,
        radius_m: int,
        max_age_s: int = 60,
    ) -> ApiResult:
        """CAMARA location-verification: is the line inside this circle?

        A yes/no question the network answers. Nothing is returned about where
        the line actually is, which is why this is the call to reach for first.
        """
        return self._call(
            "location-verification",
            "verify",
            "POST",
            "/verify",
            body={
                "device": device.to_camara(),
                "area": {
                    "areaType": "CIRCLE",
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": radius_m,
                },
                "maxAge": max_age_s,
            },
            scope="location:verify",
            subject=device.phone_number,
            cost_units=2.0,
        )

    def location_retrieve(self, device: Device, max_age_s: int = 60) -> ApiResult:
        """CAMARA location-retrieval: where is the line, as an area.

        Strictly more revealing than verification, so callers should only reach
        here once a verification has already raised the question.
        """
        return self._call(
            "location-retrieval",
            "retrieve",
            "POST",
            "/retrieve",
            body={"device": device.to_camara(), "maxAge": max_age_s},
            scope="location:retrieve",
            subject=device.phone_number,
            cost_units=4.0,
        )

    def device_reachability(self, device: Device) -> ApiResult:
        """CAMARA device-status: can this line be reached, and how."""
        return self._call(
            "device-status",
            "connectivity",
            "POST",
            "/connectivity",
            body={"device": device.to_camara()},
            scope="device:status",
            subject=device.phone_number,
            cost_units=1.0,
        )

    def device_roaming(self, device: Device) -> ApiResult:
        """CAMARA device-status: is this line roaming, and in which country."""
        return self._call(
            "device-status",
            "roaming",
            "POST",
            "/roaming",
            body={"device": device.to_camara()},
            scope="device:status",
            subject=device.phone_number,
            cost_units=1.0,
        )

    def geofencing_subscribe(
        self,
        device: Device,
        latitude: float,
        longitude: float,
        radius_m: int,
        notification_url: str,
        *,
        event_types: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> ApiResult:
        """CAMARA geofencing-subscriptions: tell me when this line leaves or enters.

        CAMARA geofencing is per line and needs that line owner's consent.
        There is no API that asks who is inside an area, which is why every
        idea in this platform enrols lines up front instead of sweeping a map.
        """
        types = event_types or ["org.camara.geofencing.v0.area-left"]
        body: Dict[str, Any] = {
            "protocol": "HTTP",
            "sink": notification_url,
            "types": types,
            "config": {
                "subscriptionDetail": {
                    "device": device.to_camara(),
                    "area": {
                        "areaType": "CIRCLE",
                        "center": {"latitude": latitude, "longitude": longitude},
                        "radius": radius_m,
                    },
                },
                "subscriptionMaxEvents": 100,
            },
        }
        if expires_at:
            body["config"]["subscriptionExpireTime"] = expires_at
        if auth_token:
            body["sinkCredential"] = {
                "credentialType": "ACCESSTOKEN",
                "accessToken": auth_token,
                "accessTokenType": "bearer",
            }
        return self._call(
            "geofencing",
            "subscribe",
            "POST",
            "/subscriptions",
            body=body,
            scope="location:geofence",
            subject=device.phone_number,
            cost_units=2.0,
        )

    def geofencing_delete(self, subscription_id: str) -> ApiResult:
        return self._call(
            "geofencing",
            "unsubscribe",
            "DELETE",
            "/subscriptions/" + subscription_id,
            cost_units=0.0,
        )

    def geofencing_list(self) -> ApiResult:
        return self._call("geofencing", "list", "GET", "/subscriptions")

    # -- Network intelligence ------------------------------------------------

    def congestion_query(
        self,
        device: Device,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> ApiResult:
        """CAMARA congestion-insights: how loaded is the cell serving this line.

        Used here to act before a problem rather than after: a crowd that is
        about to be unmanageable shows up as congestion before anyone is lost.
        """
        body: Dict[str, Any] = {"device": device.to_camara()}
        if start:
            body["start"] = start
        if end:
            body["end"] = end
        return self._call(
            "congestion",
            "query",
            "POST",
            "/query",
            body=body,
            scope="network:insights",
            subject=device.phone_number,
            cost_units=1.0,
        )

    # -- Programmable connectivity ------------------------------------------

    def qod_create_session(
        self,
        device: Device,
        profile: str,
        duration_s: int,
        app_server_ipv4: str,
        *,
        notification_url: Optional[str] = None,
    ) -> ApiResult:
        """CAMARA quality-on-demand: reserve network quality for a session.

        This is the one call in the platform that changes the network rather
        than reading it, so it is always the last step and never speculative.
        """
        body: Dict[str, Any] = {
            "qosProfile": profile,
            "device": device.to_camara(),
            "applicationServer": {"ipv4Address": app_server_ipv4},
            "duration": duration_s,
        }
        if notification_url:
            body["webhook"] = {"notificationUrl": notification_url}
        return self._call(
            "qod",
            "create-session",
            "POST",
            "/sessions",
            body=body,
            scope="network:qod",
            subject=device.phone_number,
            cost_units=8.0,
        )

    def qod_get_session(self, session_id: str) -> ApiResult:
        return self._call("qod", "get-session", "GET", "/sessions/" + session_id)

    def qod_delete_session(self, session_id: str) -> ApiResult:
        return self._call("qod", "delete-session", "DELETE", "/sessions/" + session_id)

    def qod_extend_session(self, session_id: str, additional_s: int) -> ApiResult:
        return self._call(
            "qod",
            "extend-session",
            "POST",
            "/sessions/" + session_id + "/extend",
            body={"requestedAdditionalDuration": additional_s},
            cost_units=2.0,
        )

    def slice_create(
        self,
        name: str,
        *,
        sst: int = 1,
        sd: Optional[str] = None,
        notification_url: str = "",
        area_of_service: Optional[Dict[str, Any]] = None,
    ) -> ApiResult:
        """CAMARA network-slice-management: request a slice for a service."""
        body: Dict[str, Any] = {
            "name": name,
            "networkIdentifier": {"mnc": "01", "mcc": "001"},
            "sliceInfo": {"serviceType": sst, "differentiator": sd or "AAABBB"},
            "notificationUrl": notification_url or "https://example.invalid/hook",
        }
        if area_of_service:
            body["areaOfService"] = area_of_service
        return self._call(
            "slice", "create", "POST", "/slices", body=body, cost_units=20.0
        )

    def slice_activate(self, name: str) -> ApiResult:
        return self._call(
            "slice", "activate", "POST", "/slices/" + name + "/activate", cost_units=5.0
        )

    def slice_deactivate(self, name: str) -> ApiResult:
        return self._call("slice", "deactivate", "POST", "/slices/" + name + "/deactivate")

    def slice_get(self, name: str) -> ApiResult:
        return self._call("slice", "get", "GET", "/slices/" + name)

    def slice_attach_device(
        self, device: Device, slice_id: str, *, notification_url: str = ""
    ) -> ApiResult:
        """CAMARA network-slice-device-attachment: put this line on that slice."""
        body = {
            "device": device.to_camara(),
            "sliceId": slice_id,
            "webhook": {"notificationUrl": notification_url or "https://example.invalid/hook"},
        }
        return self._call(
            "slice-attach",
            "attach",
            "POST",
            "/attachments",
            body=body,
            scope="network:slice",
            subject=device.phone_number,
            cost_units=6.0,
        )

    def slice_detach_device(self, attachment_id: str) -> ApiResult:
        return self._call(
            "slice-attach", "detach", "DELETE", "/attachments/" + attachment_id
        )

    # -- diagnostics ---------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        if not self._use_live():
            source = "simulator"
        elif self.config.mode == "hybrid":
            source = "hybrid"
        else:
            source = "live"
        return {
            "mode": self.config.mode,
            "effective_source": source,
            "dev_mode": self.config.dev_mode,
            "has_credentials": self.config.has_credentials,
            "calls_made": len(self.call_log),
        }
