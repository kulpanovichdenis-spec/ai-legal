"""
PrivacyManager — управление анонимизацией как отдельный слой (Этап 1).

Анонимизация больше не является просто флагом mode.
Каждый scenario имеет privacy_policy_id, определяющий:
  - что анонимизируется (user_message, file_context, rag, skills, tool_results);
  - что де-анонимизируется в ответе;
  - какие tools допущены к сырым данным (allow_raw_for_tools).

Текущее поведение сохранено через mapping legacy mode → privacy policy:
  - document_gen: anonymize file_context (договоры содержат ПДн)
  - consult:      anonymize file_context (могут быть документы)
  - ad_check:     без анонимизации
  - regulation:   без анонимизации

TODO (Этап 2): анонимизация user_message, если пользователь ввёл ПДн напрямую.
  Риск: user_message с ИНН/ОГРН/именами контрагентов нужен DaData tool в сыром виде.
  Решение: allow_raw_for_tools=['dadata_check'] уже заложено в политики.
"""

from __future__ import annotations

import logging
from typing import Any

from schemas_agent import PrivacyPolicy, SelectedSkill, ToolResult

logger = logging.getLogger("legal-assistant")

# ── Политики ──────────────────────────────────────────────────────────────────

_POLICIES: dict[str, PrivacyPolicy] = {

    # Стандартная политика для внешнего LLM без ПДн-документов
    "standard_external_llm": PrivacyPolicy(
        id="standard_external_llm",
        anonymize_user_message=False,  # TODO Этап 2: включить после audit user_message
        anonymize_file_context=False,
        anonymize_rag_context=False,
        anonymize_skill_content=False,
        anonymize_tool_results=False,
        deanonymize_response=False,
        allow_raw_for_tools=["dadata_check"],
        store_raw_messages=True,
        store_anonymized_messages=False,
    ),

    # Политика для работы с договорами / документами (ПДн третьих лиц)
    "document_anonymized": PrivacyPolicy(
        id="document_anonymized",
        anonymize_user_message=False,   # TODO Этап 2: оценить риск
        anonymize_file_context=True,    # договоры содержат ПДн — анонимизируем
        anonymize_rag_context=False,
        anonymize_skill_content=False,
        anonymize_tool_results=False,
        deanonymize_response=True,
        allow_raw_for_tools=["dadata_check"],  # DaData нужен сырой ИНН/ОГРН
        store_raw_messages=False,
        store_anonymized_messages=True,
    ),
}

# Mapping legacy mode → privacy policy id
_LEGACY_MODE_POLICY: dict[str, str] = {
    "document_gen": "document_anonymized",
    "consult":      "document_anonymized",  # consult тоже может получить документ
    "ad_check":     "standard_external_llm",
    "regulation":   "standard_external_llm",
}

_DEFAULT_POLICY_ID = "standard_external_llm"


# ── PrivacyManager ────────────────────────────────────────────────────────────

class PrivacyManager:
    def get_policy(self, policy_id: str) -> PrivacyPolicy:
        policy = _POLICIES.get(policy_id)
        if policy is None:
            logger.warning(
                "[PrivacyManager] unknown policy_id=%r, using default", policy_id
            )
            return _POLICIES[_DEFAULT_POLICY_ID]
        return policy

    def get_policy_for_legacy_mode(self, mode: str) -> PrivacyPolicy:
        policy_id = _LEGACY_MODE_POLICY.get(mode, _DEFAULT_POLICY_ID)
        return self.get_policy(policy_id)

    def create_anonymizer(self, policy: PrivacyPolicy):
        """Создать Anonymizer-экземпляр для запроса.

        Возвращает None если анонимизация не нужна.
        """
        if not (policy.anonymize_file_context or policy.anonymize_user_message):
            return None
        from anonymizer import Anonymizer
        return Anonymizer()

    def anonymize_user_message(
        self, text: str, policy: PrivacyPolicy, anonymizer: Any
    ) -> str:
        # TODO Этап 2: включить когда решён вопрос с allow_raw_for_tools
        if not policy.anonymize_user_message or anonymizer is None:
            return text
        return anonymizer.anonymize(text)

    def anonymize_file_context(
        self, text: str, policy: PrivacyPolicy, anonymizer: Any
    ) -> str:
        if not policy.anonymize_file_context or anonymizer is None or not text:
            return text
        return anonymizer.anonymize(text)

    def anonymize_rag_docs(
        self,
        docs: list[dict],
        policy: PrivacyPolicy,
        anonymizer: Any,
    ) -> list[dict]:
        if not policy.anonymize_rag_context or anonymizer is None:
            return docs
        result = []
        for doc in docs:
            content = doc.get("content", "")
            if content:
                anon = anonymizer.anonymize(content)
                doc = {**doc, "content": anon}
            result.append(doc)
        return result

    def anonymize_skills(
        self,
        skills: list[SelectedSkill],
        policy: PrivacyPolicy,
        anonymizer: Any,
    ) -> list[SelectedSkill]:
        if not policy.anonymize_skill_content or anonymizer is None:
            return skills
        result = []
        for s in skills:
            anon = anonymizer.anonymize(s.content)
            result.append(SelectedSkill(
                id=s.id,
                name=s.name,
                content=anon,
                priority=s.priority,
                reason=s.reason,
            ))
        return result

    def anonymize_tool_results(
        self,
        results: list[ToolResult],
        policy: PrivacyPolicy,
        anonymizer: Any,
    ) -> list[ToolResult]:
        if not policy.anonymize_tool_results or anonymizer is None:
            return results
        out = []
        for r in results:
            if r.tool_id in policy.allow_raw_for_tools:
                out.append(r)
                continue
            text = r.text_block or ""
            if text:
                anon = anonymizer.anonymize(text)
                r = ToolResult(
                    tool_id=r.tool_id,
                    activated=r.activated,
                    text_block=anon,
                    structured_data=r.structured_data,
                    reason=r.reason,
                    error=r.error,
                )
            out.append(r)
        return out

    def deanonymize_response_delta(
        self, text: str, policy: PrivacyPolicy, anonymizer: Any
    ) -> str:
        if not policy.deanonymize_response or anonymizer is None:
            return text
        return anonymizer.deanonymize(text)

    def create_response_buffer(
        self, policy: PrivacyPolicy, anonymizer: Any
    ) -> "ResponseDeanonymizeBuffer":
        """Stateful буфер для деанонимизации стримящего ответа.

        Плейсхолдер вида [ФИО_3] / [ИНН_ЮЛ_2] может прийти разорванным на
        границе двух SSE-чанков ('[ФИО_' + '3]'). Если деанонимизировать
        каждый чанк независимо — anonymizer не найдёт целиком плейсхолдер,
        и пользователь увидит «голый» плейсхолдер вместо настоящего значения.

        Буфер удерживает суффикс, начинающийся с '[' и не содержащий ']',
        до прихода следующего чанка либо до flush().
        """
        return ResponseDeanonymizeBuffer(self, policy, anonymizer)


# Лимит подвисшего буфера: если внутри [ ... ] нет ']' дольше этого числа
# символов — это не плейсхолдер, отдаём как есть, не висим вечно.
_MAX_BUFFERED_PLACEHOLDER_CHARS = 64


class ResponseDeanonymizeBuffer:
    def __init__(
        self,
        pm: "PrivacyManager",
        policy: PrivacyPolicy,
        anonymizer: Any,
    ) -> None:
        self._pm = pm
        self._policy = policy
        self._anon = anonymizer
        self._tail = ""

    def process(self, delta: str) -> str:
        """Возвращает безопасную часть текста, готовую к отправке клиенту."""
        if not delta:
            return ""
        if self._anon is None or not self._policy.deanonymize_response:
            return delta
        combined = self._tail + delta
        last_open = combined.rfind("[")
        if last_open != -1 and combined.find("]", last_open) == -1:
            # Хвост после последней '[' может быть началом плейсхолдера —
            # удерживаем его до закрывающей ']'. Лишние '[' и обратная кавычка
            # сразу перед токеном — тоже часть обёртки, их нельзя отдать раньше.
            hold_from = last_open
            while hold_from > 0 and combined[hold_from - 1] in "[`":
                hold_from -= 1
            if len(combined) - last_open > _MAX_BUFFERED_PLACEHOLDER_CHARS:
                # Слишком долго не закрывается — это не плейсхолдер.
                self._tail = ""
                return self._pm.deanonymize_response_delta(
                    combined, self._policy, self._anon,
                )
            safe = combined[:hold_from]
            self._tail = combined[hold_from:]
        else:
            safe = combined
            self._tail = ""
        if not safe:
            return ""
        return self._pm.deanonymize_response_delta(safe, self._policy, self._anon)

    def flush(self) -> str:
        """Сбросить остаток буфера в конце стрима."""
        if not self._tail:
            return ""
        out = self._pm.deanonymize_response_delta(
            self._tail, self._policy, self._anon,
        )
        self._tail = ""
        return out
