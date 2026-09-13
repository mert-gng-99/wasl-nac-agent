"""Every scenario a judge can click is a test.

The demo and the test suite assert the same thing on purpose. If a scenario
stops reaching the level it claims, that is a regression in the product, not a
stale fixture - the scenario is what the pitch deck says the agent does.
"""

from __future__ import annotations

import pytest

from core.config import AgentConfig, AppConfig, NacConfig
from core.server import _run_all, _run_scenario, build_platform
from idea import SPEC

SCENARIO_IDS = [s.id for s in SPEC.scenarios]


@pytest.fixture
def platform(tmp_path):
    config = AppConfig.from_env()
    config.nac = NacConfig(mode="simulator")
    config.agent = AgentConfig(provider="policy")
    config.db_path = str(tmp_path / "ledger.db")
    return build_platform(SPEC, config)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_scenario_reaches_its_expected_level(platform, scenario_id):
    scenario = SPEC.scenario(scenario_id)
    result = _run_scenario(platform, scenario)
    assert result["level"] == scenario.expect_level, (
        "%s produced '%s' but the scenario claims '%s'"
        % (scenario_id, result["level"], scenario.expect_level)
    )
    assert result["met_expectation"] is True


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_scenario_produces_a_human_readable_record(platform, scenario_id):
    result = _run_scenario(platform, SPEC.scenario(scenario_id))
    assert len(result["rationale"]) > 40, "a one-word rationale is not an explanation"
    assert result["action"], "every decision names a next action for a human"
    for call in result["evidence"]:
        assert call["api"]
        assert call["source"] == "simulator"


def test_the_agent_is_cheaper_than_calling_everything(platform):
    out = _run_all(platform)
    assert out["expectations_met"] == out["scenarios_run"]
    assert out["total_cost_units"] < out["cost_if_everything_called"], (
        "an agent that spends as much as calling every API has no reason to exist"
    )
    assert out["saved_pct"] > 20


def test_at_least_one_scenario_is_resolved_cheaply(platform):
    """Restraint has to be demonstrable, not just claimed in a deck."""
    out = _run_all(platform)
    cheapest = min(len(r["evidence"]) for r in out["results"])
    assert cheapest <= 2


def test_scenarios_cover_more_than_one_outcome(platform):
    out = _run_all(platform)
    levels = {r["level"] for r in out["results"]}
    assert len(levels) >= 3, "a demo that always says the same thing proves nothing"


def test_every_scenario_expectation_is_a_declared_level():
    for scenario in SPEC.scenarios:
        assert scenario.expect_level in SPEC.policy.levels


def test_spec_is_complete_enough_to_submit():
    assert SPEC.submission_title and len(SPEC.submission_title) < 160
    assert len(SPEC.submission_description) > 150
    assert 1 <= SPEC.theme_number <= 7
    assert SPEC.theme_name
    assert SPEC.consent.scopes and SPEC.consent.moment
    assert len(SPEC.honest_limits) >= 3, "a product with no stated limits is overclaiming"
    assert SPEC.buyers
    assert SPEC.repo_name


def test_demo_lines_are_all_distinct():
    numbers = [p.msisdn for p in SPEC.all_lines()]
    assert len(numbers) == len(set(numbers))
    for number in numbers:
        assert number.startswith("+"), "MSISDNs must be E.164"


def test_scenario_ids_are_unique_and_url_safe():
    ids = [s.id for s in SPEC.scenarios]
    assert len(ids) == len(set(ids))
    for scenario_id in ids:
        assert scenario_id.replace("-", "").isalnum()
