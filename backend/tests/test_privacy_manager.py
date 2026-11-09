"""
Tests: PrivacyManager

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_privacy_manager.py -v
"""

import pytest
from privacy_manager import PrivacyManager, _POLICIES, _LEGACY_MODE_POLICY


@pytest.fixture
def pm():
    return PrivacyManager()


def test_get_policy_standard(pm):
    p = pm.get_policy("standard_external_llm")
    assert p.id == "standard_external_llm"
    assert p.anonymize_file_context is False
    assert p.deanonymize_response is False


def test_get_policy_document(pm):
    p = pm.get_policy("document_anonymized")
    assert p.id == "document_anonymized"
    assert p.anonymize_file_context is True
    assert p.deanonymize_response is True


def test_get_unknown_policy_uses_default(pm):
    p = pm.get_policy("nonexistent_policy_xyz")
    assert p.id == "standard_external_llm"


def test_legacy_mode_document_gen(pm):
    p = pm.get_policy_for_legacy_mode("document_gen")
    assert p.id == "document_anonymized"


def test_legacy_mode_ad_check(pm):
    p = pm.get_policy_for_legacy_mode("ad_check")
    assert p.id == "standard_external_llm"
    assert p.anonymize_file_context is False


def test_legacy_mode_regulation(pm):
    p = pm.get_policy_for_legacy_mode("regulation")
    assert p.id == "standard_external_llm"


def test_dadata_in_allow_raw_for_tools(pm):
    p = pm.get_policy("standard_external_llm")
    assert "dadata_check" in p.allow_raw_for_tools

    p2 = pm.get_policy("document_anonymized")
    assert "dadata_check" in p2.allow_raw_for_tools


def test_anonymize_file_context_standard(pm):
    p = pm.get_policy("standard_external_llm")
    anonymizer = pm.create_anonymizer(p)
    assert anonymizer is None  # no anonymization needed


def test_anonymize_file_context_document(pm):
    p = pm.get_policy("document_anonymized")
    anonymizer = pm.create_anonymizer(p)
    assert anonymizer is not None

    text = "Договор с ИНН 7709709675 Иванов Иван"
    result = pm.anonymize_file_context(text, p, anonymizer)
    # Текст должен быть изменён (ПДн замаскированы)
    assert result != text or text == ""  # либо изменён, либо пустой


def test_deanonymize_response_no_policy(pm):
    p = pm.get_policy("standard_external_llm")
    result = pm.deanonymize_response_delta("some text", p, None)
    assert result == "some text"  # passthrough


def test_stream_buffer_restores_token_split_across_extra_brackets(pm):
    from anonymizer import Anonymizer
    policy = pm.get_policy("document_anonymized")
    anonymizer = Anonymizer()
    anonymizer._reverse["[КОМПАНИЯ_1]"] = "ООО «Астория»"
    buf = pm.create_response_buffer(policy, anonymizer)
    head = buf.process("Шапка `[[КОМП")
    tail = buf.process("АНИЯ_1]]` далее")
    flushed = buf.flush()
    assert head + tail + flushed == "Шапка ООО «Астория» далее"


def test_all_policies_have_required_fields(pm):
    for policy_id, policy in _POLICIES.items():
        assert policy.id == policy_id
        assert isinstance(policy.allow_raw_for_tools, list)


def test_all_legacy_modes_mapped():
    for mode in ("consult", "ad_check", "document_gen", "regulation"):
        assert mode in _LEGACY_MODE_POLICY, f"mode {mode!r} not in _LEGACY_MODE_POLICY"
