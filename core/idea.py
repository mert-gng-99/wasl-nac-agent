"""What one product is, declaratively.

Seven products share one agent, one CAMARA client and one dashboard. The
difference between them lives in an :class:`IdeaSpec`: the policy its agent
runs, the lines its simulator knows about, the scenarios a judge can click, the
words its operators use, and the submission metadata.

Keeping this declarative is what makes the platform claim honest. Adding an
eighth vertical means writing a policy and a spec, not another application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .agent import Case
from .simulator import LineProfile


def _first(*values: Any) -> Any:
    """First value that is not None. Zero and empty string are real answers."""
    for value in values:
        if value is not None:
            return value
    return None


@dataclass
class Scenario:
    """One clickable demo case with a stated expectation.

    ``expect_level`` is what makes the test suite meaningful: a scenario that
    does not reach its expected level is a regression, so the story a judge is
    shown is the same story CI checks.
    """

    id: str
    title: str
    subtitle: str
    expect_level: str
    build_case: Callable[[], Case]
    lines: List[LineProfile] = field(default_factory=list)
    narrative: str = ""
    teaches: str = ""

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "expect_level": self.expect_level,
            "narrative": self.narrative,
            "teaches": self.teaches,
        }


@dataclass
class LevelStyle:
    """How one outcome level should read on screen."""

    level: str
    label: str
    tone: str            # calm | watch | warn | alarm
    meaning: str = ""


@dataclass
class UiSpec:
    accent: str = "#1d6fe0"
    accent_soft: str = "#e8f0fd"
    hero_kicker: str = ""
    subject_label: str = "Line"
    case_label: str = "Case"
    run_all_label: str = "Run every scenario"
    levels: List[LevelStyle] = field(default_factory=list)
    metrics: List[Dict[str, str]] = field(default_factory=list)
    ad_hoc_placeholder: str = "+90555xxxxxxx"
    ad_hoc_help: str = "Type any number. Unregistered lines get a stable profile derived from the number itself."

    def level_map(self) -> Dict[str, Dict[str, str]]:
        return {
            style.level: {"label": style.label, "tone": style.tone, "meaning": style.meaning}
            for style in self.levels
        }


@dataclass
class ConsentPlan:
    """How this product obtains and bounds consent.

    Written down per idea because it is the question that sinks most CAMARA
    demos, and because the honest answer differs: an employment contract, a SIM
    handed over at an airport, a customer asking for a transfer.
    """

    moment: str
    scopes: List[str]
    duration_note: str
    who_consents: str
    revocation: str = "The enrolled person can withdraw at any time, which stops every check."


@dataclass
class IdeaSpec:
    slug: str
    name: str
    tagline: str
    theme_number: int
    theme_name: str
    submission_title: str
    submission_description: str
    policy: Any                          # an AgentPolicy
    scenarios: List[Scenario]
    consent: ConsentPlan
    ui: UiSpec = field(default_factory=UiSpec)
    lines: List[LineProfile] = field(default_factory=list)
    honest_limits: List[str] = field(default_factory=list)
    buyers: List[str] = field(default_factory=list)
    repo_name: str = ""
    demo_notes: str = ""

    def all_lines(self) -> List[LineProfile]:
        seen: Dict[str, LineProfile] = {}
        for profile in list(self.lines):
            seen[profile.msisdn] = profile
        for scenario in self.scenarios:
            for profile in scenario.lines:
                seen[profile.msisdn] = profile
        return list(seen.values())

    def scenario(self, scenario_id: str) -> Optional[Scenario]:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        return None

    def template_case(self, subject: str, **overrides: Any) -> "Case":
        """Build a case for an arbitrary line, using this product's own defaults.

        An ad-hoc check has to land somewhere sensible: a delivery needs a drop
        point, an exam needs a declared address, a muster needs a muster
        circle. Rather than make every caller supply geometry it cannot know,
        the first scenario acts as the template and is retargeted at the line
        the reviewer typed. Anything passed in overrides it.
        """
        template = self.scenarios[0].build_case()
        facts = dict(template.facts)
        facts.update(overrides.pop("facts", None) or {})
        params = dict(template.params)
        params.update(overrides.pop("params", None) or {})

        case = Case(
            subject=subject,
            kind=overrides.pop("kind", None) or template.kind,
            facts=facts,
            latitude=_first(overrides.pop("latitude", None), template.latitude),
            longitude=_first(overrides.pop("longitude", None), template.longitude),
            radius_m=int(_first(overrides.pop("radius_m", None), template.radius_m) or 1000),
            params=params,
            label=overrides.pop("label", None) or "ad-hoc check",
        )
        return case

    def metadata(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "tagline": self.tagline,
            "theme": {"number": self.theme_number, "name": self.theme_name},
            "submission_title": self.submission_title,
            "policy": {
                "name": self.policy.name,
                "kind": self.policy.kind,
                "levels": list(self.policy.levels),
                "budget_units": self.policy.budget_units,
                "tools": list(self.policy.tool_names),
            },
            "consent": {
                "moment": self.consent.moment,
                "scopes": list(self.consent.scopes),
                "duration_note": self.consent.duration_note,
                "who_consents": self.consent.who_consents,
                "revocation": self.consent.revocation,
            },
            "honest_limits": list(self.honest_limits),
            "buyers": list(self.buyers),
            "ui": {
                "accent": self.ui.accent,
                "accent_soft": self.ui.accent_soft,
                "hero_kicker": self.ui.hero_kicker,
                "subject_label": self.ui.subject_label,
                "case_label": self.ui.case_label,
                "run_all_label": self.ui.run_all_label,
                "levels": self.ui.level_map(),
                "level_order": [s.level for s in self.ui.levels],
                "metrics": list(self.ui.metrics),
                "ad_hoc_placeholder": self.ui.ad_hoc_placeholder,
                "ad_hoc_help": self.ui.ad_hoc_help,
            },
        }
