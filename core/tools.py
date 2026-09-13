"""CAMARA tool registry for the AI agent layer.

The agent does not call the CAMARA client directly. It chooses from this
registry, and each entry carries the three things a planner needs in order to
choose well:

*   ``cost_units`` - what the call costs. A SIM swap lookup is not free, and an
    agent that cannot see price will always call everything.
*   ``typical_latency_ms`` - what the call costs in time, which matters when a
    payment is waiting on the answer.
*   ``reveals`` - how much the call exposes about a person. ``boolean`` answers
    a yes/no question, ``area`` hands back a place, ``mutates`` changes the
    network. Ordering checks least-revealing first is a design rule here, not
    an afterthought, and the registry makes the rule inspectable.

Geometry and identifiers come from :class:`ToolContext`, not from the model.
The planner decides *which* question to ask; the system supplies the facts the
question needs. A model that has to invent latitudes will eventually invent a
wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .camara import ApiResult, CamaraClient, Device

# How much a call exposes, ordered from least to most.
REVEAL_ORDER = {"boolean": 0, "enum": 1, "area": 2, "mutates": 3}


@dataclass
class ToolContext:
    """Everything a tool needs that the model should not be inventing."""

    device: Device
    # Reference geometry for this case: a delivery drop point, a work zone, a
    # declared exam address, the centre of a pilgrim group.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_m: int = 1000
    # Free-form case facts an idea wants to expose to its tools.
    params: Dict[str, Any] = field(default_factory=dict)

    def require_point(self) -> tuple:
        if self.latitude is None or self.longitude is None:
            raise ValueError("this case has no reference point to check against")
        return (self.latitude, self.longitude)


@dataclass
class Tool:
    name: str
    api: str
    title: str
    description: str
    cost_units: float
    typical_latency_ms: int
    consent_scope: str
    reveals: str
    run: Callable[[CamaraClient, ToolContext, Dict[str, Any]], ApiResult]
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def declaration(self) -> Dict[str, Any]:
        """Structured tool metadata supplied to the model planner."""
        return {
            "name": self.name,
            "description": (
                self.description
                + " [CAMARA %s | cost %.0f units | ~%dms | reveals: %s]"
                % (self.api, self.cost_units, self.typical_latency_ms, self.reveals)
            ),
            "parameters": self.parameters,
        }

    def allowed_args(self, args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Drop model-supplied values the declared tool does not accept.

        The declaration is an allowlist, not documentation. This keeps an LLM
        from smuggling identifiers, geometry, or invented controls into a
        CAMARA request or the audit ledger.
        """
        allowed = self.parameters.get("properties", {})
        return {
            name: value
            for name, value in (args or {}).items()
            if name in allowed
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "api": self.api,
            "title": self.title,
            "description": self.description,
            "cost_units": self.cost_units,
            "typical_latency_ms": self.typical_latency_ms,
            "consent_scope": self.consent_scope,
            "reveals": self.reveals,
        }


# --- tool implementations ----------------------------------------------------


def _verify_number(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.number_verification_verify(ctx.device)


def _check_sim_swap(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.sim_swap_check(ctx.device, int(args.get("max_age_hours", 240)))


def _sim_swap_date(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.sim_swap_date(ctx.device)


def _check_device_swap(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.device_swap_check(ctx.device, int(args.get("max_age_hours", 240)))


def _check_reachability(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.device_reachability(ctx.device)


def _check_roaming(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.device_roaming(ctx.device)


def _verify_location(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    lat, lon = ctx.require_point()
    radius = ctx.radius_m
    return client.location_verify(ctx.device, float(lat), float(lon), radius)


def _retrieve_location(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.location_retrieve(ctx.device, int(args.get("max_age_s", 60)))


def _query_congestion(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    return client.congestion_query(ctx.device)


def _reserve_quality(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    profile = str(args.get("profile") or ctx.params.get("qos_profile") or "QOS_L")
    duration = int(args.get("duration_s") or ctx.params.get("qos_duration_s") or 300)
    server = str(ctx.params.get("app_server_ipv4") or "10.20.30.40")
    return client.qod_create_session(ctx.device, profile, duration, server)


def _watch_area(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    lat, lon = ctx.require_point()
    radius = ctx.radius_m
    sink = str(ctx.params.get("webhook_url") or "https://example.invalid/geofence")
    event = str(args.get("event") or "left")
    types = [
        "org.camara.geofencing.v0.area-entered"
        if event == "entered"
        else "org.camara.geofencing.v0.area-left"
    ]
    return client.geofencing_subscribe(
        ctx.device, lat, lon, radius, sink, event_types=types
    )


def _attach_slice(client: CamaraClient, ctx: ToolContext, args: Dict[str, Any]) -> ApiResult:
    slice_id = str(args.get("slice_id") or ctx.params.get("slice_id") or "emergency-slice")
    return client.slice_attach_device(ctx.device, slice_id)


# --- registry ----------------------------------------------------------------

_MAX_AGE_SCHEMA = {
    "type": "object",
    "properties": {
        "max_age_hours": {
            "type": "integer",
            "description": (
                "Only report a change inside this many hours. Narrow windows "
                "separate a takeover from an ordinary upgrade months ago."
            ),
        }
    },
}

_LOCATION_SCHEMA = {
    "type": "object",
    "properties": {},
}

ALL_TOOLS: List[Tool] = [
    Tool(
        name="verify_number",
        api="number-verification",
        title="Confirm the line on the phone",
        description=(
            "Ask the network to confirm the phone number really belongs to the "
            "device making the request. Replaces an SMS code and cannot be "
            "phished, because nothing is typed by the user."
        ),
        cost_units=1.0,
        typical_latency_ms=220,
        consent_scope="identity:verify",
        reveals="boolean",
        run=_verify_number,
    ),
    Tool(
        name="check_sim_swap",
        api="sim-swap",
        title="Has the SIM changed recently",
        description=(
            "Ask whether the SIM behind this line changed inside a time window. "
            "A change hours before a high-value action is the classic shape of "
            "an account takeover."
        ),
        cost_units=3.0,
        typical_latency_ms=380,
        consent_scope="fraud:sim-swap",
        reveals="boolean",
        run=_check_sim_swap,
        parameters=_MAX_AGE_SCHEMA,
    ),
    Tool(
        name="sim_swap_date",
        api="sim-swap",
        title="When did the SIM last change",
        description=(
            "Retrieve the timestamp of the most recent SIM change. Costs the "
            "same as the yes/no check but tells you how close the change sits "
            "to the event, which is what separates suspicion from evidence."
        ),
        cost_units=3.0,
        typical_latency_ms=380,
        consent_scope="fraud:sim-swap",
        reveals="enum",
        run=_sim_swap_date,
    ),
    Tool(
        name="check_device_swap",
        api="device-swap",
        title="Has the handset changed recently",
        description=(
            "Ask whether the device behind this line changed inside a window. "
            "Read together with SIM swap it distinguishes a new phone from a "
            "new person holding the account."
        ),
        cost_units=3.0,
        typical_latency_ms=360,
        consent_scope="fraud:device-swap",
        reveals="boolean",
        run=_check_device_swap,
        parameters=_MAX_AGE_SCHEMA,
    ),
    Tool(
        name="check_reachability",
        api="device-status",
        title="Can the line be reached",
        description=(
            "Ask whether the line is reachable on data, reachable only by SMS, "
            "or not reachable at all. This is what tells a dead battery apart "
            "from someone in trouble, and it is cheap enough to ask first."
        ),
        cost_units=1.0,
        typical_latency_ms=200,
        consent_scope="device:status",
        reveals="enum",
        run=_check_reachability,
    ),
    Tool(
        name="check_roaming",
        api="device-status",
        title="Is the line roaming, and where",
        description=(
            "Ask whether the line is on a visited network and in which country. "
            "Roaming on its own is a migrant worker's ordinary life, so treat "
            "it as context, never as a red flag by itself."
        ),
        cost_units=1.0,
        typical_latency_ms=210,
        consent_scope="device:status",
        reveals="enum",
        run=_check_roaming,
    ),
    Tool(
        name="verify_location",
        api="location-verification",
        title="Is the line inside this area",
        description=(
            "Ask a yes/no question about an area. The network answers TRUE, "
            "FALSE, PARTIAL or UNKNOWN and returns no coordinates, so this is "
            "the location question to ask before any other."
        ),
        cost_units=2.0,
        typical_latency_ms=320,
        consent_scope="location:verify",
        reveals="boolean",
        run=_verify_location,
        parameters=_LOCATION_SCHEMA,
    ),
    Tool(
        name="retrieve_location",
        api="location-retrieval",
        title="Where is the line",
        description=(
            "Retrieve the area the line is in. Strictly more revealing than a "
            "verification, so only reach for it once a cheaper answer has "
            "raised a real question, such as a rescue team needing somewhere "
            "to start."
        ),
        cost_units=4.0,
        typical_latency_ms=520,
        consent_scope="location:retrieve",
        reveals="area",
        run=_retrieve_location,
        parameters={
            "type": "object",
            "properties": {
                "max_age_s": {"type": "integer", "description": "Accept a fix this many seconds old."}
            },
        },
    ),
    Tool(
        name="query_congestion",
        api="congestion-insights",
        title="How loaded is the serving cell",
        description=(
            "Ask how congested the cell serving this line is. Lets the agent "
            "act before a crowd becomes a problem, and explains a dropped "
            "connection that would otherwise look like someone cheating."
        ),
        cost_units=1.0,
        typical_latency_ms=280,
        consent_scope="network:insights",
        reveals="enum",
        run=_query_congestion,
    ),
    Tool(
        name="watch_area",
        api="geofencing-subscriptions",
        title="Notify me when the line leaves or enters an area",
        description=(
            "Subscribe to area-left or area-entered events for this one line. "
            "CAMARA geofencing is per line and consented, so this can only be "
            "used on people who were enrolled, never to sweep a map."
        ),
        cost_units=2.0,
        typical_latency_ms=340,
        consent_scope="location:geofence",
        reveals="area",
        run=_watch_area,
        parameters={
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "enum": ["left", "entered"],
                    "description": "Which crossing to be told about.",
                },
            },
        },
    ),
    Tool(
        name="reserve_quality",
        api="quality-on-demand",
        title="Reserve network quality for this line",
        description=(
            "Open a quality-on-demand session so a video call or telemetry "
            "stream survives a congested cell. This changes the network rather "
            "than reading it, so it is a last step and never speculative."
        ),
        cost_units=8.0,
        typical_latency_ms=700,
        consent_scope="network:qod",
        reveals="mutates",
        run=_reserve_quality,
        parameters={
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "QoS profile name, for example QOS_L."},
                "duration_s": {"type": "integer", "description": "Session length in seconds."},
            },
        },
    ),
    Tool(
        name="attach_slice",
        api="network-slice-device-attachment",
        title="Put this line on a dedicated slice",
        description=(
            "Attach the line to a network slice reserved for emergency "
            "responders. The heaviest action available and the most expensive, "
            "so it is for a declared incident only."
        ),
        cost_units=6.0,
        typical_latency_ms=900,
        consent_scope="network:slice",
        reveals="mutates",
        run=_attach_slice,
        parameters={
            "type": "object",
            "properties": {"slice_id": {"type": "string"}},
        },
    ),
]

_BY_NAME = {tool.name: tool for tool in ALL_TOOLS}


class ToolRegistry:
    """The tools one idea's agent is allowed to use, in a fixed order."""

    def __init__(self, names: Optional[List[str]] = None) -> None:
        selected = names or [tool.name for tool in ALL_TOOLS]
        unknown = [name for name in selected if name not in _BY_NAME]
        if unknown:
            raise ValueError("unknown tools: " + ", ".join(unknown))
        self.tools: List[Tool] = [_BY_NAME[name] for name in selected]
        self._by_name = {tool.name: tool for tool in self.tools}

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self.tools)

    def get(self, name: str) -> Tool:
        if name not in self._by_name:
            raise KeyError("tool '%s' is not available to this agent" % name)
        return self._by_name[name]

    def names(self) -> List[str]:
        return [tool.name for tool in self.tools]

    def apis(self) -> List[str]:
        seen: List[str] = []
        for tool in self.tools:
            if tool.api not in seen:
                seen.append(tool.api)
        return seen

    def declarations(self) -> List[Dict[str, Any]]:
        return [tool.declaration() for tool in self.tools]

    def catalogue(self) -> List[Dict[str, Any]]:
        return [tool.summary() for tool in self.tools]

    def cheapest_first(self) -> List[Tool]:
        """Least revealing first, then cheapest. The order a careful human uses."""
        return sorted(
            self.tools,
            key=lambda t: (REVEAL_ORDER.get(t.reveals, 9), t.cost_units, t.typical_latency_ms),
        )

    def total_cost_if_all_called(self) -> float:
        return round(sum(tool.cost_units for tool in self.tools), 2)
