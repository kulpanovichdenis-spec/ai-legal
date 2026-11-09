"""
DaDataTool — wrapper над существующей DaData MCP логикой (Этап 1).

Не переписывает DaData-логику, а делегирует в _run_dadata_check из main.py.
На этапе 2 можно перенести логику сюда полностью.

Allowed scenarios:
  legal_consultation, contract_analysis, claim_drafting,
  notice_drafting, document_from_contract, consumer_return_claim
"""

from __future__ import annotations

import logging
import re

from schemas_agent import RequestContext, ScenarioConfig, ToolResult
from tools.base import BaseTool

logger = logging.getLogger("legal-assistant")

_ALLOWED_SCENARIO_IDS = {
    "legal_consultation",
    "contract_analysis",
    "claim_drafting",
    "notice_drafting",
    "document_from_contract",
    "consumer_return_claim",
}

# Паттерны для быстрой эвристики наличия идентификаторов контрагента
_INN_RE = re.compile(r"\b\d{10}\b|\b\d{12}\b")
_OGRN_RE = re.compile(r"\b\d{13}\b|\b\d{15}\b")


class DaDataTool(BaseTool):
    id = "dadata_check"

    async def should_activate(
        self,
        ctx: RequestContext,
        scenario: ScenarioConfig,
    ) -> tuple[bool, str]:
        if scenario.id not in _ALLOWED_SCENARIO_IDS:
            return False, f"scenario {scenario.id!r} not in allowed list"

        combined = (ctx.message or "") + " " + (ctx.raw_file_context or "")

        # Быстрая эвристика: есть ли ИНН/ОГРН
        if _INN_RE.search(combined) or _OGRN_RE.search(combined):
            return True, "найден ИНН/ОГРН в сообщении или файле"

        # Дополнительно: dadata_policy определит окончательно при execute()
        # Для consult дополнительная проверка через should_check_counterparty_consult
        if scenario.legacy_mode_group == "consult":
            try:
                from dadata_policy import should_check_counterparty_consult
                if should_check_counterparty_consult(ctx.message):
                    return True, "контекст запроса указывает на проверку контрагента"
            except Exception:
                pass

        if scenario.legacy_mode_group == "document_gen":
            return True, "document scenario — dadata проверяется по policy"

        return False, "ИНН/ОГРН не найдены, дополнительных сигналов нет"

    async def execute(
        self,
        ctx: RequestContext,
        scenario: ScenarioConfig,
    ) -> ToolResult:
        try:
            from dadata_service import _run_dadata_check

            raw_combined = (ctx.message or "") + " " + " ".join(
                filter(None, [ctx.raw_file_context])
            )
            mode = scenario.legacy_mode_group
            text_block, check_result = await _run_dadata_check(
                mode=mode,
                message=ctx.message or "",
                raw_text=raw_combined,
                correlation_id=ctx.request_id,
                history=(ctx.metadata or {}).get("dialog_history"),
            )

            if check_result:
                return ToolResult(
                    tool_id=self.id,
                    activated=True,
                    text_block=text_block or None,
                    structured_data=check_result,
                    reason="контрагент проверен через DaData",
                )

            return ToolResult(
                tool_id=self.id,
                activated=False,
                text_block=None,
                structured_data=None,
                reason="DaData policy: проверка не требуется",
            )

        except Exception as exc:
            logger.error("[DaDataTool] execute error: %s", exc, exc_info=True)
            return ToolResult(
                tool_id=self.id,
                activated=False,
                text_block=None,
                structured_data=None,
                reason="ошибка DaData tool",
                error=str(exc)[:500],
            )
