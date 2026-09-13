"""Runtime configuration for the Network-as-Code agent platform.

Everything is environment driven so the same bundle runs three ways:

  NAC_MODE=simulator   no credentials needed, deterministic network responses
  NAC_MODE=live        real Nokia Network-as-Code calls over the RapidAPI gateway
  NAC_MODE=hybrid      live where credentials allow, simulator for the rest

The simulator exists because the hackathon organisers recommend simulator
numbers, and because a judged demo must never depend on a third party being
awake. Every simulated response carries ``"source": "simulator"`` so nothing
can quietly pass itself off as a real network answer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["simulator", "live", "hybrid"]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class NacConfig:
    """Nokia Network-as-Code gateway settings."""

    mode: Mode = "simulator"
    rapid_key: str = ""
    # Nokia fronts every CAMARA API with its own RapidAPI host. Defaults are the
    # production hosts published on networkascode.nokia.io; dev_mode switches to
    # the sandbox hosts that accept simulator MSISDNs.
    dev_mode: bool = True
    timeout_s: float = 12.0

    def host(self, api: str) -> str:
        return _HOSTS_DEV[api] if self.dev_mode else _HOSTS_PROD[api]

    def base_url(self, api: str) -> str:
        return f"https://{self.host(api)}"

    @property
    def has_credentials(self) -> bool:
        return bool(self.rapid_key)

    @classmethod
    def from_env(cls) -> "NacConfig":
        mode = (os.getenv("NAC_MODE") or "simulator").strip().lower()
        if mode not in {"simulator", "live", "hybrid"}:
            mode = "simulator"
        key = (os.getenv("NAC_RAPIDAPI_KEY") or os.getenv("NAC_API_KEY") or "").strip()
        # Asking for live mode without a key is a configuration mistake, not a
        # reason to crash a demo: fall back and let /api/health report it.
        if mode == "live" and not key:
            mode = "simulator"
        return cls(
            mode=mode,  # type: ignore[arg-type]
            rapid_key=key,
            dev_mode=_env_bool("NAC_DEV_MODE", True),
            timeout_s=_env_float("NAC_TIMEOUT_S", 12.0),
        )


@dataclass
class AgentConfig:
    """AI agent layer settings.

    The planner is an LLM producing typed proposals over the CAMARA tool registry.
    ``provider`` picks the model backend; ``policy`` is the deterministic
    fallback planner that runs when no model key is present, so the prototype
    is demonstrable offline and the test suite stays deterministic.
    """

    provider: Literal["gemini", "policy"] = "policy"
    # Google AI Studio retires model ids without notice: gemini-2.5-flash,
    # which the guide names, now answers 404 on generateContent. Pinned to a
    # current flash model and overridable with AGENT_MODEL.
    model: str = "gemini-3.5-flash"
    api_key: str = ""
    temperature: float = 0.1
    max_steps: int = 8
    # Budget is enforced by the runtime, never by the model. A planner that
    # wants to spend more than the case is worth simply gets refused.
    default_budget_units: float = 100.0
    request_timeout_s: float = 30.0

    @property
    def llm_enabled(self) -> bool:
        return self.provider == "gemini" and bool(self.api_key)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip()
        # A key is not an instruction to incur model calls. Deployments and CI
        # can carry ambient credentials; turning Gemini on requires an explicit
        # operator choice as well as a key.
        provider = (os.getenv("AGENT_PROVIDER") or "policy").strip().lower()
        if provider not in {"gemini", "policy"}:
            provider = "policy"
        if provider == "gemini" and not key:
            provider = "policy"
        return cls(
            provider=provider,  # type: ignore[arg-type]
            model=(os.getenv("AGENT_MODEL") or "gemini-3.5-flash").strip(),
            api_key=key,
            temperature=_env_float("AGENT_TEMPERATURE", 0.1),
            max_steps=_env_int("AGENT_MAX_STEPS", 8),
            default_budget_units=_env_float("AGENT_BUDGET_UNITS", 100.0),
            request_timeout_s=_env_float("AGENT_TIMEOUT_S", 30.0),
        )


@dataclass
class AppConfig:
    nac: NacConfig = field(default_factory=NacConfig.from_env)
    agent: AgentConfig = field(default_factory=AgentConfig.from_env)
    db_path: str = "data/ledger.db"
    scenario_seed: int = 20260913
    public_base_url: str = ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            nac=NacConfig.from_env(),
            agent=AgentConfig.from_env(),
            db_path=(os.getenv("DB_PATH") or "data/ledger.db").strip(),
            scenario_seed=_env_int("SCENARIO_SEED", 20260913),
            public_base_url=(os.getenv("PUBLIC_BASE_URL") or "").strip(),
        )


# --- Nokia Network-as-Code gateway hosts -------------------------------------
# Taken from the published Network-as-Code gateway. Each CAMARA API is a
# separate RapidAPI host; the sandbox hosts are the ones that answer for the
# simulator MSISDNs handed out on the developer portal.

_HOSTS_PROD = {
    "qod": "quality-of-service-on-demand.p-eu.rapidapi.com",
    "location-verification": "location-verification.p-eu.rapidapi.com",
    "location-retrieval": "location-retrieval.p-eu.rapidapi.com",
    "geofencing": "geofencing-subscriptions.p-eu.rapidapi.com",
    "slice": "network-slicing.p-eu.rapidapi.com",
    "slice-attach": "network-slice-device-attachment.p-eu.rapidapi.com",
    "device-status": "device-status.p-eu.rapidapi.com",
    "congestion": "congestion-insights.p-eu.rapidapi.com",
    "sim-swap": "sim-swap.p-eu.rapidapi.com",
    "device-swap": "device-swap.p-eu.rapidapi.com",
    "number-verification": "number-verification.p-eu.rapidapi.com",
}

_HOSTS_DEV = {
    "qod": "qos-on-demand2.p-eu.rapidapi.com",
    "location-verification": "location-verification5.p-eu.rapidapi.com",
    "location-retrieval": "location-retrieval3.p-eu.rapidapi.com",
    "geofencing": "geofencing-subscriptions.p-eu.rapidapi.com",
    "slice": "network-slicing2.p-eu.rapidapi.com",
    "slice-attach": "device-application-attach.p-eu.rapidapi.com",
    "device-status": "device-status1.p-eu.rapidapi.com",
    "congestion": "congestion-insights.p-eu.rapidapi.com",
    "sim-swap": "simswap.p-eu.rapidapi.com",
    "device-swap": "device-swap.p-eu.rapidapi.com",
    "number-verification": "number-verification.p-eu.rapidapi.com",
}

# Path prefixes. Nokia mounts some APIs at the host root and some under the
# CAMARA path, so the prefix belongs next to the host.
API_PREFIX = {
    "qod": "",
    "location-verification": "",
    "location-retrieval": "",
    "geofencing": "/geofencing-subscriptions/v0.3",
    "slice": "",
    "slice-attach": "",
    "device-status": "",
    "congestion": "",
    "sim-swap": "/sim-swap/sim-swap/v0",
    "device-swap": "/device-swap/v0",
    "number-verification": "/number-verification/v1",
}
