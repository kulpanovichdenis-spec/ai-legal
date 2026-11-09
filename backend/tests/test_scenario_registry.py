"""
Tests: ScenarioRegistry

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_scenario_registry.py -v
"""

import pytest
from scenario_registry import ScenarioRegistry, _LEGACY_MODE_MAP


@pytest.fixture
def registry():
    return ScenarioRegistry()


def test_get_existing_scenario(registry):
    sc = registry.get("legal_consultation")
    assert sc.id == "legal_consultation"
    assert sc.legacy_mode_group == "consult"
    assert sc.enabled is True


def test_get_unknown_scenario_falls_back_to_default(registry):
    sc = registry.get("nonexistent_scenario_xyz")
    assert sc.id == "legal_consultation"  # default


def test_get_default_returns_legal_consultation(registry):
    sc = registry.get_default()
    assert sc.id == "legal_consultation"


def test_from_legacy_mode_consult(registry):
    sc = registry.from_legacy_mode("consult")
    assert sc.id == "legal_consultation"
    assert sc.legacy_mode_group == "consult"


def test_from_legacy_mode_regulation(registry):
    sc = registry.from_legacy_mode("regulation")
    assert sc.id == "regulation_lookup"


def test_from_legacy_mode_ad_check(registry):
    sc = registry.from_legacy_mode("ad_check")
    assert sc.id == "ad_check_general"


def test_from_legacy_mode_document_gen(registry):
    sc = registry.from_legacy_mode("document_gen")
    assert sc.id == "contract_analysis"


def test_all_enabled_returns_list(registry):
    scenarios = registry.all_enabled()
    assert len(scenarios) >= 9
    ids = [s.id for s in scenarios]
    assert "legal_consultation" in ids
    assert "ad_check_general" in ids
    assert "claim_drafting" in ids
    assert "consumer_return_claim" in ids


def test_all_scenarios_have_prompt_id(registry):
    for sc in registry.all_enabled():
        assert sc.prompt_id, f"scenario {sc.id!r} has no prompt_id"


def test_all_scenarios_have_privacy_policy(registry):
    for sc in registry.all_enabled():
        assert sc.privacy_policy_id, f"scenario {sc.id!r} has no privacy_policy_id"


def test_legacy_mode_for(registry):
    assert registry.legacy_mode_for("ad_check_general") == "ad_check"
    assert registry.legacy_mode_for("regulation_lookup") == "regulation"


def test_all_modes_covered():
    modes = {"consult", "regulation", "ad_check", "document_gen", "presentation"}
    assert modes == set(_LEGACY_MODE_MAP.keys())


def test_singleton():
    a = ScenarioRegistry.get_instance()
    b = ScenarioRegistry.get_instance()
    assert a is b
