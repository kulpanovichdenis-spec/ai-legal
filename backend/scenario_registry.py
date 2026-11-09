"""
ScenarioRegistry — реестр сценариев агентской архитектуры (Этап 1).

На этапе 1 конфигурация хранится в Python-словаре.
На этапе 2 можно вынести в YAML или БД.

Mapping legacy mode → scenario:
  consult      → legal_consultation
  regulation   → regulation_lookup
  ad_check     → ad_check_general
  document_gen → contract_analysis (primary fallback для document work)
"""

from __future__ import annotations

import logging
from typing import ClassVar

from schemas_agent import ScenarioConfig

logger = logging.getLogger("legal-assistant")

# ── Skill ids (ссылки на имена из таблицы skills) ────────────────────────────

_SK_CLAIM_TEMPLATES    = "claim-templates"
_SK_NOTICE_TEMPLATES   = "notice-templates"
_SK_INTERNAL_RESOURCES = "Внутренние ресурсы компании"
_SK_CONSUMER_RETURN    = "Возврат и замена товара потребителю"
_SK_FORM_NAVIGATOR     = "Навигатор по типовым формам"
_SK_DOC_PREP           = "Подготовка юридических документов"
_SK_AD_CHECKLIST       = "Чек-листы проверки рекламы ЛС/БАД/Косметика"

# ── Scenarios ─────────────────────────────────────────────────────────────────

_SCENARIOS: dict[str, ScenarioConfig] = {

    "legal_consultation": ScenarioConfig(
        id="legal_consultation",
        label="Юридическая консультация",
        legacy_mode_group="consult",
        prompt_id="consult_base",
        required_skill_ids=[],
        optional_skill_ids=[_SK_INTERNAL_RESOURCES],
        allowed_tool_ids=["dadata_check"],
        rag_strategy="default",
        privacy_policy_id="standard_external_llm",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=30,
    ),

    "regulation_lookup": ScenarioConfig(
        id="regulation_lookup",
        label="Поиск по внутренним регламентам",
        legacy_mode_group="regulation",
        prompt_id="regulation_lookup_base",
        required_skill_ids=[],
        optional_skill_ids=[_SK_INTERNAL_RESOURCES],
        allowed_tool_ids=[],
        rag_strategy="default",
        privacy_policy_id="standard_external_llm",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=30,
    ),

    "ad_check_general": ScenarioConfig(
        id="ad_check_general",
        label="Проверка рекламы",
        legacy_mode_group="ad_check",
        prompt_id="ad_check_base",
        required_skill_ids=[_SK_AD_CHECKLIST],
        optional_skill_ids=[],
        allowed_tool_ids=["product_lookup"],
        rag_strategy="default",
        privacy_policy_id="standard_external_llm",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),

    "contract_analysis": ScenarioConfig(
        id="contract_analysis",
        label="Анализ договора / проверка рисков",
        legacy_mode_group="document_gen",
        prompt_id="contract_analysis_base",
        required_skill_ids=[],
        optional_skill_ids=[_SK_DOC_PREP],
        allowed_tool_ids=["dadata_check"],
        rag_strategy="default",
        privacy_policy_id="document_anonymized",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),

    "claim_drafting": ScenarioConfig(
        id="claim_drafting",
        label="Подготовка претензии",
        legacy_mode_group="document_gen",
        prompt_id="claim_drafting_base",
        required_skill_ids=[_SK_CLAIM_TEMPLATES],
        optional_skill_ids=[_SK_DOC_PREP, _SK_CONSUMER_RETURN],
        allowed_tool_ids=["dadata_check"],
        rag_strategy="default",
        privacy_policy_id="document_anonymized",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),

    "notice_drafting": ScenarioConfig(
        id="notice_drafting",
        label="Подготовка уведомления / письма",
        legacy_mode_group="document_gen",
        prompt_id="notice_drafting_base",
        required_skill_ids=[_SK_NOTICE_TEMPLATES],
        optional_skill_ids=[_SK_DOC_PREP],
        allowed_tool_ids=["dadata_check"],
        rag_strategy="default",
        privacy_policy_id="document_anonymized",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),

    "document_from_contract": ScenarioConfig(
        id="document_from_contract",
        label="Подготовка документа на основе договора",
        legacy_mode_group="document_gen",
        prompt_id="document_from_contract_base",
        required_skill_ids=[],
        optional_skill_ids=[_SK_DOC_PREP, _SK_NOTICE_TEMPLATES, _SK_CLAIM_TEMPLATES],
        allowed_tool_ids=["dadata_check"],
        rag_strategy="default",
        privacy_policy_id="document_anonymized",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),

    "template_form_navigation": ScenarioConfig(
        id="template_form_navigation",
        label="Навигация по типовым формам",
        legacy_mode_group="document_gen",
        prompt_id="consult_base",  # навигация — консультативный сценарий; Навигатор-скилл несёт основную логику
        required_skill_ids=[_SK_FORM_NAVIGATOR],
        optional_skill_ids=[_SK_DOC_PREP],
        allowed_tool_ids=[],
        rag_strategy="default",
        privacy_policy_id="standard_external_llm",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),

    "legal_business_presentation": ScenarioConfig(
        id="legal_business_presentation",
        label="Презентация для бизнеса",
        legacy_mode_group="presentation",
        prompt_id="presentation_base",
        required_skill_ids=[],
        optional_skill_ids=[],
        allowed_tool_ids=[],
        rag_strategy="default",
        privacy_policy_id="standard_external_llm",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=30,
    ),

    "consumer_return_claim": ScenarioConfig(
        id="consumer_return_claim",
        label="Возврат / замена товара потребителю",
        legacy_mode_group="document_gen",
        prompt_id="claim_drafting_base",
        required_skill_ids=[_SK_CONSUMER_RETURN],
        optional_skill_ids=[_SK_CLAIM_TEMPLATES, _SK_DOC_PREP],
        allowed_tool_ids=["dadata_check"],
        rag_strategy="default",
        privacy_policy_id="document_anonymized",
        memory_policy_id="default_limited",
        model_policy_id=None,
        timeout_s=None,
        max_history_messages=20,
    ),
}

# ── Legacy mode → scenario mapping ───────────────────────────────────────────

_LEGACY_MODE_MAP: dict[str, str] = {
    "consult":      "legal_consultation",
    "regulation":   "regulation_lookup",
    "ad_check":     "ad_check_general",
    "document_gen": "contract_analysis",  # общий fallback для document_gen
    "presentation": "legal_business_presentation",
    # "auto" не маппируется — роутер решает сам
}

_DEFAULT_SCENARIO_ID = "legal_consultation"


# ── Registry class ────────────────────────────────────────────────────────────

class ScenarioRegistry:
    _instance: ClassVar[ScenarioRegistry | None] = None

    def __init__(self, scenarios: dict[str, ScenarioConfig] | None = None) -> None:
        self._scenarios = scenarios or _SCENARIOS

    # Singleton для production; тесты создают свой экземпляр
    @classmethod
    def get_instance(cls) -> "ScenarioRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, scenario_id: str) -> ScenarioConfig:
        scenario = self._scenarios.get(scenario_id)
        if scenario is None:
            logger.warning(
                "[ScenarioRegistry] unknown scenario_id=%r, falling back to default", scenario_id
            )
            return self.get_default()
        return scenario

    def get_default(self) -> ScenarioConfig:
        return self._scenarios[_DEFAULT_SCENARIO_ID]

    def from_legacy_mode(self, mode: str) -> ScenarioConfig:
        scenario_id = _LEGACY_MODE_MAP.get(mode, _DEFAULT_SCENARIO_ID)
        logger.debug("[ScenarioRegistry] legacy mode=%r → scenario_id=%r", mode, scenario_id)
        return self._scenarios[scenario_id]

    def all_enabled(self) -> list[ScenarioConfig]:
        return [s for s in self._scenarios.values() if s.enabled]

    def scenario_ids(self) -> list[str]:
        return list(self._scenarios.keys())

    def legacy_mode_for(self, scenario_id: str) -> str:
        return self.get(scenario_id).legacy_mode_group
