"""
Tests: _parse_scenario_ids, _parse_activation_condition (main.py)
"""

import pytest
from main import _parse_scenario_ids, _parse_activation_condition


# ── _parse_scenario_ids ────────────────────────────────────────────────────────

def test_scenario_ids_empty_string():
    assert _parse_scenario_ids("") is None

def test_scenario_ids_empty_array():
    assert _parse_scenario_ids("[]") is None

def test_scenario_ids_single():
    result = _parse_scenario_ids('["legal_consultation"]')
    import json
    assert json.loads(result) == ["legal_consultation"]

def test_scenario_ids_multiple():
    result = _parse_scenario_ids('["ad_check_general", "claim_drafting"]')
    import json
    assert json.loads(result) == ["ad_check_general", "claim_drafting"]

def test_scenario_ids_not_json():
    assert _parse_scenario_ids("не JSON") is None

def test_scenario_ids_numbers_not_strings():
    assert _parse_scenario_ids("[1, 2, 3]") is None

def test_scenario_ids_mixed_types():
    assert _parse_scenario_ids('["legal_consultation", 42]') is None

def test_scenario_ids_object_not_array():
    assert _parse_scenario_ids('{"id": "legal_consultation"}') is None


# ── _parse_activation_condition ───────────────────────────────────────────────

def test_activation_always():
    assert _parse_activation_condition("always") == "always"

def test_activation_disabled():
    assert _parse_activation_condition("disabled") == "disabled"

def test_activation_keyword_with_words():
    result = _parse_activation_condition("keyword:претензия|иск|заявление")
    assert result == "keyword:претензия|иск|заявление"

def test_activation_keyword_single_word():
    assert _parse_activation_condition("keyword:реклама") == "keyword:реклама"

def test_activation_keyword_empty_falls_back():
    # "keyword:" без слов — невалидно, fallback к "always"
    assert _parse_activation_condition("keyword:") == "always"

def test_activation_unknown_value_falls_back():
    assert _parse_activation_condition("что-то левое") == "always"

def test_activation_empty_string_falls_back():
    assert _parse_activation_condition("") == "always"
