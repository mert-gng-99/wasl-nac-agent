"""Turning CAMARA answers into facts.

Every policy needs the same first step: read a CAMARA response and name what it
found. ``read_signal`` does that once, so seven policies do not each re-pluck
``verificationResult`` out of a payload and disagree about what PARTIAL means.

Policies then add their own derivations on top. Amana cares how many hours sit
between a SIM change and a payment; Rafiq cares whether a silent line was last
seen inside the group. That judgement stays in the policy. The plucking lives
here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .camara import ApiResult

# CAMARA location verification answers, ordered by how much they let you act.
LOCATION_CONFIDENT = {"TRUE", "FALSE"}
LOCATION_UNCERTAIN = {"PARTIAL", "UNKNOWN"}


def read_signal(tool: str, result: ApiResult) -> Dict[str, Any]:
    """Normalise one CAMARA answer into named facts."""
    data = result.data or {}

    if tool == "verify_number":
        return {"number_verified": bool(data.get("devicePhoneNumberVerified"))}

    if tool == "check_sim_swap":
        return {"sim_swapped_in_window": bool(data.get("swapped"))}

    if tool == "sim_swap_date":
        stamp = data.get("latestSimChange")
        hours = _hours_since(stamp)
        return {
            "sim_change_at": stamp,
            "sim_change_hours_ago": hours,
            "sim_change_known": stamp is not None,
        }

    if tool == "check_device_swap":
        return {"device_swapped_in_window": bool(data.get("swapped"))}

    if tool == "device_swap_date":
        stamp = data.get("latestDeviceChange")
        return {"device_change_at": stamp, "device_change_hours_ago": _hours_since(stamp)}

    if tool == "check_reachability":
        status = str(data.get("connectivityStatus") or "UNKNOWN")
        return {
            "reachability": status,
            "reachable": status in {"CONNECTED_DATA", "CONNECTED_SMS"},
            "reachable_on_data": status == "CONNECTED_DATA",
            "silent": status == "NOT_CONNECTED",
        }

    if tool == "check_roaming":
        countries = data.get("countryName") or []
        return {
            "roaming": bool(data.get("roaming")),
            "roaming_country": (countries[0] if countries else None),
            "roaming_country_code": data.get("countryCode"),
        }

    if tool == "verify_location":
        verdict = str(data.get("verificationResult") or "UNKNOWN")
        return {
            "location_result": verdict,
            "location_inside": verdict == "TRUE",
            "location_outside": verdict == "FALSE",
            # PARTIAL and UNKNOWN are real answers. Rounding them to a yes or a
            # no is how people get wrongly accused, so they stay visible.
            "location_uncertain": verdict in LOCATION_UNCERTAIN,
            "location_match_rate": data.get("matchRate"),
            "location_fix_age": data.get("lastLocationTime"),
        }

    if tool == "retrieve_location":
        area = data.get("area") or {}
        centre = area.get("center") or {}
        return {
            "last_known_lat": centre.get("latitude"),
            "last_known_lon": centre.get("longitude"),
            "last_known_radius_m": area.get("radius"),
            "last_known_at": data.get("lastLocationTime"),
            "has_last_known_point": bool(centre),
        }

    if tool == "query_congestion":
        windows = data.get("result") or []
        first = windows[0] if windows else {}
        level = str(first.get("congestionLevel") or "unknown")
        return {
            "congestion": level,
            "congestion_confidence": first.get("confidenceLevel"),
            "cell_crowded": level in {"medium", "high"},
            "cell_saturated": level == "high",
        }

    if tool == "watch_area":
        return {
            "geofence_id": data.get("id"),
            "geofence_status": data.get("status"),
            "geofence_types": data.get("types"),
            "geofence_active": str(data.get("status") or "").upper() in {"ACTIVE", "ACTIVATION_REQUESTED"},
        }

    if tool == "reserve_quality":
        return {
            "qod_session_id": data.get("sessionId"),
            "qod_status": data.get("qosStatus"),
            "qod_profile": data.get("qosProfile"),
            "quality_reserved": str(data.get("qosStatus") or "").upper() in {"AVAILABLE", "REQUESTED"},
        }

    if tool == "attach_slice":
        return {
            "slice_attachment_id": data.get("nac_resource_id"),
            "slice_status": data.get("status"),
            "on_priority_slice": str(data.get("status") or "").upper() == "ATTACHED",
        }

    return {tool + "_raw": data}


def _hours_since(stamp: Optional[str]) -> Optional[float]:
    if not stamp:
        return None
    text = str(stamp).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - moment
    return round(delta.total_seconds() / 3600.0, 2)
