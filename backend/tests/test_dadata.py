"""
Тесты для DaData/MCP интеграции.

Запуск (из директории backend/):
    pytest tests/test_dadata.py -v
"""
import sys
import os

# Добавляем backend/ в PYTHONPATH для импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import AsyncMock, patch

from dadata_placeholder import is_anonymized_placeholder, text_contains_id_placeholders
from dadata_policy import (
    can_call_dadata_mcp, extract_identifiers, DadataMcpPolicyInput,
)
from dadata_comparator import compare_with_contract, ComparisonResult
from dadata_mcp_client import DadataCompanyResult
from dadata_normalizer import (
    normalize_for_documents, normalize_for_consultation,
    normalize_skipped_documents, normalize_skipped_consultation,
)


# ── Placeholder detector ──────────────────────────────────────────────

class TestIsAnonymizedPlaceholder:
    def test_bracket_inn(self):
        assert is_anonymized_placeholder("[INN_1]") is True

    def test_bracket_company(self):
        assert is_anonymized_placeholder("[COMPANY_2]") is True

    def test_double_brace_address(self):
        assert is_anonymized_placeholder("{{ADDRESS_1}}") is True

    def test_angle_ogrn(self):
        assert is_anonymized_placeholder("<OGRN_1>") is True

    def test_angle_ogrnip(self):
        assert is_anonymized_placeholder("<OGRNIP_1>") is True

    def test_real_inn_10(self):
        assert is_anonymized_placeholder("7721581040") is False

    def test_real_ogrn_13(self):
        assert is_anonymized_placeholder("1027700132195") is False

    def test_real_ogrnip_15(self):
        assert is_anonymized_placeholder("304500116000157") is False

    def test_none(self):
        assert is_anonymized_placeholder(None) is False

    def test_empty(self):
        assert is_anonymized_placeholder("") is False

    def test_bracket_kpp(self):
        assert is_anonymized_placeholder("[KPP_1]") is True

    def test_bracket_bank_details(self):
        assert is_anonymized_placeholder("[BANK_DETAILS_1]") is True


class TestTextContainsIdPlaceholders:
    def test_text_with_inn_placeholder(self):
        assert text_contains_id_placeholders("ИНН контрагента: [INN_1]") is True

    def test_text_with_ogrn_placeholder(self):
        assert text_contains_id_placeholders("ОГРН: [OGRN_1]") is True

    def test_text_without_placeholders(self):
        assert text_contains_id_placeholders("ИНН: 7707083893") is False

    def test_empty_text(self):
        assert text_contains_id_placeholders("") is False


# ── Policy layer ──────────────────────────────────────────────────────

class TestCanCallDadataMcp:
    def _inp(self, **kw) -> DadataMcpPolicyInput:
        defaults = {
            "mode": "documents",
            "intent": "documents_verify_party_details",
            "inn": "7707083893",
            "ogrn": "",
            "ogrnip": "",
            "contains_anonymized_placeholders": False,
        }
        defaults.update(kw)
        return DadataMcpPolicyInput(**defaults)

    def test_documents_claim_real_inn(self):
        inp = self._inp(intent="documents_fill_document_requisites")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is True
        assert decision.reason == "allowed"

    def test_documents_notice_real_ogrn(self):
        inp = self._inp(intent="documents_verify_notice_address", inn="", ogrn="1027700132195")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is True

    def test_documents_summary_no_identifiers(self):
        inp = self._inp(intent="documents_counterparty_screening", inn="", ogrn="", ogrnip="")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is False
        assert decision.reason == "no_identifier"

    def test_documents_placeholder_inn(self):
        inp = self._inp(contains_anonymized_placeholders=True, inn="")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is False
        assert decision.reason == "anonymized_placeholder"

    def test_consultation_real_inn(self):
        inp = self._inp(mode="consultation", intent="consultation_counterparty_check")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is True

    def test_consultation_real_ogrn(self):
        inp = self._inp(
            mode="consultation", intent="consultation_counterparty_check",
            inn="", ogrn="1027700132195",
        )
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is True

    def test_consultation_real_ogrnip(self):
        inp = self._inp(
            mode="consultation", intent="consultation_counterparty_check",
            inn="", ogrn="", ogrnip="304500116000157",
        )
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is True

    def test_consultation_no_identifier(self):
        inp = self._inp(
            mode="consultation", intent="consultation_counterparty_check",
            inn="", ogrn="", ogrnip="",
        )
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is False
        assert decision.reason == "no_identifier"

    def test_consultation_unsupported_intent(self):
        inp = self._inp(mode="consultation", intent="documents_verify_party_details")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is False
        assert decision.reason == "unsupported_intent"

    def test_unsupported_mode(self):
        inp = self._inp(mode="ad_check")  # type: ignore
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is False
        assert decision.reason == "unsupported_mode"

    def test_inn_is_placeholder_value(self):
        inp = self._inp(inn="[INN_1]")
        decision = can_call_dadata_mcp(inp)
        assert decision.allowed is False
        assert decision.reason == "anonymized_placeholder"


# ── Identifier extraction ─────────────────────────────────────────────

class TestExtractIdentifiers:
    def test_extract_inn(self):
        inn, ogrn, ogrnip, _ = extract_identifiers("ИНН 7721581040 договор")
        assert inn == "7721581040"
        assert ogrn == ""

    def test_extract_ogrn(self):
        inn, ogrn, ogrnip, _ = extract_identifiers("ОГРН 1027700132195")
        assert ogrn == "1027700132195"

    def test_extract_ogrnip(self):
        inn, ogrn, ogrnip, _ = extract_identifiers("ОГРНИП 304500116000157")
        assert ogrnip == "304500116000157"

    def test_has_placeholders(self):
        _, _, _, has_ph = extract_identifiers("ИНН контрагента [INN_1]")
        assert has_ph is True

    def test_no_placeholders(self):
        _, _, _, has_ph = extract_identifiers("ИНН 7707083893")
        assert has_ph is False


# ── Comparison ────────────────────────────────────────────────────────

class TestCompareWithContract:
    def _dadata(self, **kw) -> DadataCompanyResult:
        defaults = dict(
            found=True, name_full="ПАО СБЕРБАНК", name_short="СБЕРБАНК",
            inn="7707083893", ogrn="1027700132195", kpp="773601001",
            address="г Москва, ул Вавилова, д 19",
            address_full="117312, г Москва, ул Вавилова, д 19",
            status="ACTIVE", entity_type="LEGAL", okved="64.19", okved_name="",
            error="",
        )
        defaults.update(kw)
        return DadataCompanyResult(**defaults)

    def test_no_discrepancies(self):
        result = compare_with_contract(
            self._dadata(),
            contract_inn="7707083893",
            contract_ogrn="1027700132195",
            contract_address="г Москва, ул Вавилова, д 19",
        )
        assert result.discrepancies == []

    def test_inn_mismatch_critical(self):
        result = compare_with_contract(self._dadata(), contract_inn="7721581040")
        critical = [d for d in result.discrepancies if d.field == "ИНН"]
        assert len(critical) == 1
        assert critical[0].severity == "critical"

    def test_ogrn_mismatch_critical(self):
        result = compare_with_contract(self._dadata(), contract_ogrn="1234567890123")
        critical = [d for d in result.discrepancies if d.field == "ОГРН/ОГРНИП"]
        assert len(critical) == 1
        assert critical[0].severity == "critical"

    def test_inactive_status_critical(self):
        result = compare_with_contract(self._dadata(status="LIQUIDATED"))
        critical = [d for d in result.discrepancies if d.field == "статус"]
        assert len(critical) == 1
        assert critical[0].severity == "critical"

    def test_address_mismatch_warning(self):
        result = compare_with_contract(
            self._dadata(),
            contract_address="г. Москва, Тверская ул., д. 1",
        )
        warns = [d for d in result.discrepancies if d.field == "адрес"]
        assert len(warns) == 1
        assert warns[0].severity == "warning"

    def test_not_found_no_discrepancies(self):
        result = compare_with_contract(
            DadataCompanyResult(found=False),
            contract_inn="7707083893",
        )
        assert result.discrepancies == []

    def test_has_address_mismatch_flag(self):
        result = compare_with_contract(
            self._dadata(),
            contract_address="г. Москва, Тверская ул., д. 1",
        )
        assert result.has_address_mismatch is True


# ── Prompt context safety ─────────────────────────────────────────────

class TestPromptContextSafety:
    """Проверяем, что raw response и секреты не попадают в LLM-контекст."""

    def test_documents_block_uses_requisites_without_promising_a_card(self):
        r = DadataCompanyResult(
            found=True, name_full="ООО ТЕСТ", inn="7707083893",
            ogrn="1027700132195", kpp="770701001",
            address="Москва", address_full="117312, Москва", status="ACTIVE",
            entity_type="LEGAL", okved="64.19", okved_name="", name_short="ТЕСТ", error="",
        )
        block = normalize_for_documents(r, "documents_verify_notice_address")
        assert "карточке в интерфейсе" not in block
        assert "шапку и текст документа" in block
        assert "7707083893" in block

    def test_normalize_for_documents_no_raw_json(self):
        r = DadataCompanyResult(
            found=True, name_full="ООО ТЕСТ", inn="7707083893",
            ogrn="1027700132195", kpp="770701001",
            address="Москва", address_full="117312, Москва", status="ACTIVE",
            entity_type="LEGAL", okved="64.19", okved_name="", name_short="ТЕСТ", error="",
        )
        block = normalize_for_documents(r, "documents_verify_party_details")
        assert "jsonrpc" not in block
        assert "structuredContent" not in block
        assert "licenses" not in block

    def test_normalize_for_consultation_no_raw_json(self):
        r = DadataCompanyResult(
            found=True, name_full="ООО ТЕСТ", inn="7707083893",
            ogrn="1027700132195", kpp="", address="Москва", address_full="",
            status="ACTIVE", entity_type="LEGAL", okved="", okved_name="",
            name_short="ТЕСТ", error="",
        )
        block = normalize_for_consultation(r, "ИНН")
        assert "jsonrpc" not in block
        assert "managers" not in block

    def test_skipped_documents_no_identifier(self):
        block = normalize_skipped_documents("no_identifier")
        assert "пропущено" in block
        assert "нет ИНН" in block

    def test_skipped_consultation_no_identifier(self):
        block = normalize_skipped_consultation("no_identifier")
        assert "пропущено" in block
        assert "запроси у пользователя" in block

    def test_skipped_documents_placeholder(self):
        block = normalize_skipped_documents("anonymized_placeholder")
        assert "анонимизированы" in block

    def test_address_mismatch_recommendation(self):
        r = DadataCompanyResult(
            found=True, name_full="ООО ТЕСТ", inn="7707083893",
            ogrn="1027700132195", kpp="", address="Москва, Тверская 1",
            address_full="117312, Москва, Тверская 1",
            status="ACTIVE", entity_type="LEGAL", okved="", okved_name="",
            name_short="ТЕСТ", error="",
        )
        comparison = compare_with_contract(r, contract_address="Москва, Ленинский 10")
        block = normalize_for_documents(r, "documents_verify_notice_address", comparison)
        assert "оба адреса" in block.lower() or "рекомендация" in block.lower()


class TestIsBareCounterpartyCheck:
    """Голая проверка контрагента → подавление дублирующего текстового ответа."""

    def _bare(self, msg):
        from dadata_policy import is_bare_counterparty_check
        return is_bare_counterparty_check(msg)

    def test_plain_inn(self):
        assert self._bare("проверь контрагента 7811280136") is True

    def test_named_company_with_inn(self):
        assert self._bare("проверь ООО Миалин ИНН 7811280136") is True

    def test_can_work_with(self):
        assert self._bare("можно работать с 7811280136") is True

    def test_with_extra_task_not_bare(self):
        assert self._bare("проверь 7811280136 и подготовь претензию") is False

    def test_with_question_not_bare(self):
        assert self._bare("проверь 7811280136, можно ли работать по предоплате?") is False

    def test_no_identifier_not_bare(self):
        assert self._bare("проверь контрагента") is False

    def test_not_a_check_not_bare(self):
        assert self._bare("составь договор поставки") is False


# ── Follow-up: голый ИНН после просьбы проверить контрагента ─────────

class TestConsultIdentifierFollowup:
    """Сообщение из одного ИНН/ОГРН запускает DaData, только если диалог
    уже просил проверить контрагента или ассистент только что спросил номер."""

    @pytest.fixture
    def dadata_on(self, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "dadata_api_key", "test-key")
        monkeypatch.setattr(settings, "dadata_secret_key", "test-secret")

    @pytest.fixture
    def find_calls(self, monkeypatch):
        calls: list[tuple[str, str, str]] = []

        async def fake_find(*, inn: str = "", ogrn: str = "", ogrnip: str = ""):
            calls.append((inn, ogrn, ogrnip))
            return DadataCompanyResult(found=False)

        monkeypatch.setattr("dadata_service._dadata_client.find_company", fake_find)
        return calls

    async def _run(self, message: str, history: list[dict] | None):
        from dadata_service import _run_dadata_check
        return await _run_dadata_check(
            mode="consult",
            message=message,
            raw_text=message,
            correlation_id="test",
            history=history,
        )

    @pytest.mark.asyncio
    async def test_inn_only_after_counterparty_request_runs_check(self, dadata_on, find_calls):
        history = [
            {"role": "user", "content": "проверь контрагента"},
            {"role": "assistant", "content": "Укажите ИНН, ОГРН или ОГРНИП контрагента."},
            {"role": "user", "content": "7825673514"},
        ]
        block, card = await self._run("7825673514", history)
        assert find_calls == [("7825673514", "", "")]
        assert card is None
        assert block

    @pytest.mark.asyncio
    async def test_punctuated_inn_after_assistant_asked_for_number(self, dadata_on, find_calls):
        history = [
            {"role": "user", "content": "нужна справка по компании"},
            {"role": "assistant", "content": "Пришлите ИНН или ОГРН, чтобы я проверил контрагента."},
        ]
        await self._run("«7825673514».", history)
        assert find_calls == [("7825673514", "", "")]

    @pytest.mark.asyncio
    async def test_inn_only_without_prior_request_does_not_run(self, dadata_on, find_calls):
        block, card = await self._run("7825673514", history=None)
        assert find_calls == []
        assert block == ""
        assert card is None

    @pytest.mark.asyncio
    async def test_digits_inside_unrelated_message_do_not_run(self, dadata_on, find_calls):
        history = [
            {"role": "user", "content": "как посчитать неустойку"},
            {"role": "assistant", "content": "По ст. 395 ГК РФ."},
        ]
        block, card = await self._run(
            "в приложении 4 сумма 7825673514 рублей",
            history,
        )
        assert find_calls == []
        assert block == ""
        assert card is None

    @pytest.mark.asyncio
    async def test_first_message_without_number_stays_no_identifier(self, dadata_on, find_calls):
        block, card = await self._run("проверь контрагента", history=None)
        assert find_calls == []
        assert card is None
        assert "запроси у пользователя" in block


class TestCounterpartyCardEvent:
    """Карточка уходит клиенту только в консультации. В документных сценариях
    реквизиты остаются в промпте, но SSE-события карточки нет."""

    def test_document_scenarios_do_not_emit_card_event(self):
        from dadata_policy import client_counterparty_event
        card = {"schemaVersion": 1, "blocks": [{"type": "counterparty", "data": {}}]}
        for scenario_id in (
            "notice_drafting",
            "contract_analysis",
            "claim_drafting",
            "document_from_contract",
            "consumer_return_claim",
        ):
            assert client_counterparty_event(scenario_id, card) is None, scenario_id

    def test_consult_bare_check_still_emits_card_event(self):
        from dadata_policy import client_counterparty_event
        card = {"schemaVersion": 1, "blocks": [{"type": "counterparty", "data": {"inn": "1"}}]}
        event = client_counterparty_event("legal_consultation", card)
        assert event == {"type": "counterparty_check", "data": card}

    def test_notice_drafting_stays_document_anonymized(self):
        from scenario_registry import ScenarioRegistry
        scenario = ScenarioRegistry().get("notice_drafting")
        assert scenario.privacy_policy_id == "document_anonymized"


@pytest.mark.asyncio
async def test_dadata_tool_forwards_dialog_history(monkeypatch):
    from schemas_agent import RequestContext
    from scenario_registry import ScenarioRegistry
    from tools.dadata_tool import DaDataTool

    seen: dict = {}

    async def fake_run(mode, message, raw_text, correlation_id, history=None):
        seen["history"] = history
        return "", None

    monkeypatch.setattr("dadata_service._run_dadata_check", fake_run)
    history = [
        {"role": "user", "content": "проверь контрагента"},
        {"role": "assistant", "content": "Укажите ИНН."},
    ]
    ctx = RequestContext(
        request_id="test",
        user_id="u1",
        message="7825673514",
        conversation_id="c1",
        legacy_mode="consult",
        channel="web",
        external_user_id=None,
        external_chat_id=None,
        external_message_id=None,
        channel_metadata={},
        attachments=[],
        file_context=None,
        raw_file_context=None,
        history_summary=None,
        metadata={"dialog_history": history},
    )
    await DaDataTool().execute(ctx, ScenarioRegistry().get("legal_consultation"))
    assert seen["history"] == history
