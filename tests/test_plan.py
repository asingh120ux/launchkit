import copy
import os

import pytest

from launchkit.plan import load_plan, validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_PATH = os.path.join(ROOT, "plan.yaml")


@pytest.fixture(scope="module")
def plan():
    return load_plan(PLAN_PATH)


def test_validate_passes_on_plan_yaml(plan):
    result = validate(plan, ROOT)
    assert result.problems == [], "\n".join(str(p) for p in result.problems)


def test_validate_fails_when_a_budget_is_over(plan):
    over = copy.deepcopy(plan)
    over["meta"]["campaigns"][0]["daily_budget"] += 100  # +$3,000/month pushes Meta past its cap
    result = validate(over, ROOT)
    messages = [p.message for p in result.problems]
    assert any("over its monthly_budget" in m for m in messages), messages


def test_validate_fails_when_total_is_over(plan):
    over = copy.deepcopy(plan)
    over["account"]["total_monthly_budget"] = 1000
    result = validate(over, ROOT)
    assert any("total_monthly_budget" in p.message for p in result.problems)


def test_validate_fails_on_unknown_ad_name(plan):
    bad = copy.deepcopy(plan)
    bad["meta"]["campaigns"][0]["ad_sets"][0]["ads"].append("No_Such_Ad")
    result = validate(bad, ROOT)
    assert any("No_Such_Ad" in p.message and "not in meta.ads" in p.message for p in result.problems)


def test_cut_campaign_is_shown_and_not_counted(plan):
    result = validate(plan, ROOT)
    assert "Search_NonBrand_UK [SEARCH]  CUT" in result.tree
