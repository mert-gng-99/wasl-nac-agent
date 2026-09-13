"""Deterministic network simulator behind the CAMARA client.

The hackathon organisers recommend simulator numbers, and a judged demo must
not depend on a third party being reachable. So every CAMARA API used by the
platform has a simulated counterpart here that answers in the real CAMARA
response shape.

Two rules it follows:

*   **Deterministic.** A line's behaviour comes from its registered profile, or
    failing that from a hash of its own number. The same number always answers
    the same way, so a demo can be rehearsed and a test can assert.
*   **Never disguised.** Simulated answers are tagged ``source: simulator`` by
    the client that calls this, and the dashboard shows that tag, so nothing
    here can be mistaken for a real network answer.

A ``LineProfile`` is how a scenario is written: set ``sim_swap_hours_ago=6`` and
that line now looks like a takeover to every part of the system that asks.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# --- CAMARA enumerations -----------------------------------------------------

REACHABLE_DATA = "CONNECTED_DATA"
REACHABLE_SMS = "CONNECTED_SMS"
NOT_REACHABLE = "NOT_CONNECTED"

VERIFY_TRUE = "TRUE"
VERIFY_FALSE = "FALSE"
VERIFY_PARTIAL = "PARTIAL"
VERIFY_UNKNOWN = "UNKNOWN"

CONGESTION_LOW = "low"
CONGESTION_MEDIUM = "medium"
CONGESTION_HIGH = "high"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Small enough to keep local."""
    from math import asin, cos, radians, sin, sqrt

    r = 6371000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = (
        sin(d_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    )
    return 2 * r * asin(sqrt(a))


@dataclass
class LineProfile:
    """How one MSISDN behaves across every CAMARA API.

    Everything is optional; unset fields fall back to the benign default, which
    is a line that verifies, is reachable on data, is not roaming, has not been
    swapped, and sits in an uncongested cell.
    """

    msisdn: str
    label: str = ""
    number_verified: bool = True
    sim_swap_hours_ago: Optional[float] = None
    device_swap_hours_ago: Optional[float] = None
    reachability: str = REACHABLE_DATA
    roaming: bool = False
    country_code: int = 90
    country_name: str = "TUR"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_accuracy_m: int = 2000
    location_age_s: int = 40
    congestion: str = CONGESTION_LOW
    congestion_confidence: int = 90
    # When set, location_verify answers this regardless of geometry. Used for
    # the UNKNOWN case, which is a real CAMARA answer and the one most demos
    # pretend does not exist.
    force_verification: Optional[str] = None
    notes: str = ""

    def location(self) -> Optional[Tuple[float, float]]:
        if self.latitude is None or self.longitude is None:
            return None
        return (self.latitude, self.longitude)


class NetworkSimulator:
    """Answers CAMARA calls for registered and unregistered lines alike."""

    def __init__(self, profiles: Optional[List[LineProfile]] = None) -> None:
        self.profiles: Dict[str, LineProfile] = {}
        for profile in profiles or []:
            self.register(profile)
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._qod_sessions: Dict[str, Dict[str, Any]] = {}
        self._slices: Dict[str, Dict[str, Any]] = {}
        self._attachments: Dict[str, Dict[str, Any]] = {}

    # -- profile registry ----------------------------------------------------

    def register(self, profile: LineProfile) -> LineProfile:
        self.profiles[_normalise(profile.msisdn)] = profile
        return profile

    def register_all(self, profiles: List[LineProfile]) -> None:
        for profile in profiles:
            self.register(profile)

    def update(self, msisdn: str, **changes: Any) -> LineProfile:
        """Mutate a line mid-demo, which is how the live walkthrough works.

        Flipping ``reachability`` to NOT_CONNECTED on stage is what turns a
        quiet dashboard into an escalation the judges can watch happen.
        """
        profile = self.profile_for(msisdn)
        for key, value in changes.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        self.profiles[_normalise(profile.msisdn)] = profile
        return profile

    def profile_for(self, msisdn: Optional[str]) -> LineProfile:
        key = _normalise(msisdn or "")
        if key in self.profiles:
            return self.profiles[key]
        return self._derive(key)

    def _derive(self, msisdn: str) -> LineProfile:
        """Stable pseudo-profile for a line nobody registered.

        Derived from the number itself so behaviour is reproducible without
        holding state for every MSISDN a reviewer might type in.
        """
        seed = int(hashlib.sha256(msisdn.encode("utf-8")).hexdigest()[:8], 16)
        profile = LineProfile(msisdn=msisdn, label="unregistered line")
        profile.number_verified = seed % 17 != 0
        profile.reachability = (
            NOT_REACHABLE if seed % 11 == 0
            else REACHABLE_SMS if seed % 7 == 0
            else REACHABLE_DATA
        )
        profile.roaming = seed % 5 == 0
        if seed % 13 == 0:
            profile.sim_swap_hours_ago = 4 + (seed % 40)
        if seed % 19 == 0:
            profile.device_swap_hours_ago = 2 + (seed % 30)
        profile.congestion = (
            CONGESTION_HIGH if seed % 9 == 0
            else CONGESTION_MEDIUM if seed % 4 == 0
            else CONGESTION_LOW
        )
        return profile

    # -- dispatch ------------------------------------------------------------

    def respond(
        self,
        api: str,
        operation: str,
        body: Dict[str, Any],
        *,
        subject: Optional[str] = None,
    ) -> Dict[str, Any]:
        msisdn = subject or _subject_from_body(body)
        profile = self.profile_for(msisdn)
        key = (api, operation)

        handler = _HANDLERS.get(key)
        if handler is None:
            # Operation names are also passed as endpoint paths on the hybrid
            # fallback path, so match on a suffix before giving up.
            for (h_api, h_op), candidate in _HANDLERS.items():
                if h_api == api and h_op in operation:
                    handler = candidate
                    break
        if handler is None:
            return {"simulated": True, "api": api, "operation": operation}
        return handler(self, profile, body)

    # -- handlers ------------------------------------------------------------

    def _number_verification(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"devicePhoneNumberVerified": profile.number_verified}

    def _sim_swap_check(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        max_age = float(body.get("maxAge") or 240)
        hours = profile.sim_swap_hours_ago
        swapped = hours is not None and hours <= max_age
        return {"swapped": swapped}

    def _sim_swap_date(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        hours = profile.sim_swap_hours_ago
        if hours is None:
            # CAMARA returns null when the operator holds no swap record.
            return {"latestSimChange": None}
        return {"latestSimChange": _iso(_now() - timedelta(hours=hours))}

    def _device_swap_check(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        max_age = float(body.get("maxAge") or 240)
        hours = profile.device_swap_hours_ago
        swapped = hours is not None and hours <= max_age
        return {"swapped": swapped}

    def _device_swap_date(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        hours = profile.device_swap_hours_ago
        if hours is None:
            return {"latestDeviceChange": None}
        return {"latestDeviceChange": _iso(_now() - timedelta(hours=hours))}

    def _location_verify(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        if profile.force_verification:
            return {
                "verificationResult": profile.force_verification,
                "lastLocationTime": _iso(_now() - timedelta(seconds=profile.location_age_s)),
            }
        area = (body.get("area") or {})
        centre = (area.get("center") or {})
        radius = float(area.get("radius") or 2000)
        here = profile.location()
        if here is None or "latitude" not in centre:
            return {"verificationResult": VERIFY_UNKNOWN}

        distance = _haversine_m(here[0], here[1], float(centre["latitude"]), float(centre["longitude"]))
        accuracy = float(profile.location_accuracy_m)
        if distance + accuracy <= radius:
            result, match = VERIFY_TRUE, 98
        elif distance - accuracy > radius:
            result, match = VERIFY_FALSE, 4
        else:
            # The line's uncertainty circle straddles the boundary. CAMARA has
            # a word for this and acting as if it were TRUE is how people get
            # wrongly accused, so it is surfaced rather than rounded away.
            result, match = VERIFY_PARTIAL, 55
        return {
            "verificationResult": result,
            "matchRate": match,
            "lastLocationTime": _iso(_now() - timedelta(seconds=profile.location_age_s)),
        }

    def _location_retrieve(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        here = profile.location()
        if here is None:
            return {"lastLocationTime": None, "area": None}
        return {
            "lastLocationTime": _iso(_now() - timedelta(seconds=profile.location_age_s)),
            "area": {
                "areaType": "CIRCLE",
                "center": {"latitude": here[0], "longitude": here[1]},
                "radius": profile.location_accuracy_m,
            },
        }

    def _connectivity(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"connectivityStatus": profile.reachability}

    def _roaming(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "roaming": profile.roaming,
            "countryCode": profile.country_code,
            "countryName": [profile.country_name],
        }

    def _congestion(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        start = _now()
        window = [
            {
                "timeIntervalStart": _iso(start),
                "timeIntervalEnd": _iso(start + timedelta(minutes=15)),
                "congestionLevel": profile.congestion,
                "confidenceLevel": profile.congestion_confidence,
            }
        ]
        return {"result": window}

    def _qod_create(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(uuid.uuid4())
        record = {
            "sessionId": session_id,
            "qosProfile": body.get("qosProfile"),
            "qosStatus": "AVAILABLE",
            "duration": body.get("duration"),
            "startedAt": _iso(_now()),
            "expiresAt": _iso(_now() + timedelta(seconds=int(body.get("duration") or 300))),
            "device": body.get("device"),
        }
        self._qod_sessions[session_id] = record
        return record

    def _qod_get(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self._qod_sessions:
            return {"qosStatus": "UNAVAILABLE"}
        return list(self._qod_sessions.values())[-1]

    def _qod_delete(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        if self._qod_sessions:
            self._qod_sessions.pop(list(self._qod_sessions)[-1], None)
        return {"deleted": True}

    def _geofence_subscribe(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        sub_id = str(uuid.uuid4())
        record = {
            "id": sub_id,
            "types": body.get("types"),
            "sink": body.get("sink"),
            "status": "ACTIVE",
            "startsAt": _iso(_now()),
            "config": body.get("config"),
        }
        self._subscriptions[sub_id] = record
        return record

    def _geofence_list(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": list(self._subscriptions.values())}

    def _geofence_delete(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"deleted": True}

    def _slice_create(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        name = body.get("name") or str(uuid.uuid4())
        record = {
            "name": name,
            "sliceId": "slice-" + name,
            "state": "AVAILABLE",
            "sliceInfo": body.get("sliceInfo"),
            "createdAt": _iso(_now()),
        }
        self._slices[name] = record
        return record

    def _slice_activate(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "OPERATING"}

    def _slice_generic(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "AVAILABLE"}

    def _slice_attach(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        attachment_id = str(uuid.uuid4())
        record = {
            "nac_resource_id": attachment_id,
            "status": "ATTACHED",
            "sliceId": body.get("sliceId"),
            "device": body.get("device"),
            "attachedAt": _iso(_now()),
        }
        self._attachments[attachment_id] = record
        return record

    def _slice_detach(self, profile: LineProfile, body: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "DETACHED"}


def _normalise(msisdn: str) -> str:
    cleaned = (msisdn or "").strip().replace(" ", "").replace("-", "")
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def _subject_from_body(body: Dict[str, Any]) -> str:
    if "phoneNumber" in body:
        return str(body["phoneNumber"])
    device = body.get("device")
    if isinstance(device, dict) and "phoneNumber" in device:
        return str(device["phoneNumber"])
    return ""


_HANDLERS = {
    ("number-verification", "verify"): NetworkSimulator._number_verification,
    ("sim-swap", "check"): NetworkSimulator._sim_swap_check,
    ("sim-swap", "retrieve-date"): NetworkSimulator._sim_swap_date,
    ("device-swap", "check"): NetworkSimulator._device_swap_check,
    ("device-swap", "retrieve-date"): NetworkSimulator._device_swap_date,
    ("location-verification", "verify"): NetworkSimulator._location_verify,
    ("location-retrieval", "retrieve"): NetworkSimulator._location_retrieve,
    ("device-status", "connectivity"): NetworkSimulator._connectivity,
    ("device-status", "roaming"): NetworkSimulator._roaming,
    ("congestion", "query"): NetworkSimulator._congestion,
    ("qod", "create-session"): NetworkSimulator._qod_create,
    ("qod", "get-session"): NetworkSimulator._qod_get,
    ("qod", "delete-session"): NetworkSimulator._qod_delete,
    ("qod", "extend-session"): NetworkSimulator._qod_get,
    ("geofencing", "subscribe"): NetworkSimulator._geofence_subscribe,
    ("geofencing", "list"): NetworkSimulator._geofence_list,
    ("geofencing", "unsubscribe"): NetworkSimulator._geofence_delete,
    ("slice", "create"): NetworkSimulator._slice_create,
    ("slice", "activate"): NetworkSimulator._slice_activate,
    ("slice", "deactivate"): NetworkSimulator._slice_generic,
    ("slice", "get"): NetworkSimulator._slice_generic,
    ("slice-attach", "attach"): NetworkSimulator._slice_attach,
    ("slice-attach", "detach"): NetworkSimulator._slice_detach,
}
