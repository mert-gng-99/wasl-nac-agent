"""Shared platform behind all seven MENA Ignite prototypes.

The same core runs every product: one CAMARA client for the Nokia
Network-as-Code gateway, one cost-aware agent loop, one consent gate, one
evidence ledger, one dashboard. A product is a policy plus a spec.
"""

from .agent import Agent, AgentStep, Budget, Case, Decision, LlmPlanner, PolicyPlanner
from .camara import ApiResult, CamaraClient, CamaraError, ConsentError, Device
from .config import AgentConfig, AppConfig, NacConfig
from .consent import ConsentGrant, ConsentLedger
from .events import EventBus
from .idea import ConsentPlan, IdeaSpec, LevelStyle, Scenario, UiSpec
from .ledger import DecisionLedger
from .server import Platform, app_from_env, build_platform, create_app
from .simulator import LineProfile, NetworkSimulator
from .tools import ALL_TOOLS, Tool, ToolContext, ToolRegistry

__all__ = [
    "Agent", "AgentStep", "Budget", "Case", "Decision", "LlmPlanner", "PolicyPlanner",
    "ApiResult", "CamaraClient", "CamaraError", "ConsentError", "Device",
    "AgentConfig", "AppConfig", "NacConfig",
    "ConsentGrant", "ConsentLedger",
    "EventBus",
    "ConsentPlan", "IdeaSpec", "LevelStyle", "Scenario", "UiSpec",
    "DecisionLedger",
    "Platform", "app_from_env", "build_platform", "create_app",
    "LineProfile", "NetworkSimulator",
    "ALL_TOOLS", "Tool", "ToolContext", "ToolRegistry",
]

__version__ = "1.0.0"
