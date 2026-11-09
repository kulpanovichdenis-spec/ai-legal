"""
ToolRegistry — формальный выбор и выполнение tools (Этап 1).

Убирает hardcoded if-mode из main.py.
Каждый tool объявляет should_activate() и execute() сам.
Trace фиксирует: tool_id, activated/skipped, reason, error.
"""

from __future__ import annotations

import logging

from schemas_agent import RequestContext, ScenarioConfig, ToolResult
from agent_trace import AgentTrace
from tools.base import BaseTool, ToolPhase
from tools.dadata_tool import DaDataTool
from tools.product_lookup_tool import ProductLookupTool

logger = logging.getLogger("legal-assistant")


class ToolRegistry:
    def __init__(self, db_path: str) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._db_path = db_path
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(DaDataTool())
        self.register(ProductLookupTool(self._db_path))

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.id] = tool

    def get(self, tool_id: str) -> BaseTool | None:
        """Публичный доступ к зарегистрированному инструменту по id."""
        return self._tools.get(tool_id)

    def filter_by_phase(
        self,
        tools: list[BaseTool],
        phase: ToolPhase,
    ) -> list[BaseTool]:
        """Отфильтровать список инструментов по фазе исполнения."""
        return [t for t in tools if getattr(t, "phase", "prompt") == phase]

    async def select_tools(
        self,
        scenario: ScenarioConfig,
        ctx: RequestContext,
        trace: AgentTrace | None = None,
    ) -> list[BaseTool]:
        selected: list[BaseTool] = []

        for tool_id in scenario.allowed_tool_ids:
            tool = self._tools.get(tool_id)
            if tool is None:
                logger.warning("[ToolRegistry] unknown tool_id=%r in scenario=%s", tool_id, scenario.id)
                continue

            try:
                activate, reason = await tool.should_activate(ctx, scenario)
            except Exception as exc:
                logger.error("[ToolRegistry] should_activate error for %r: %s", tool_id, exc)
                activate, reason = False, f"error: {exc}"

            if activate:
                selected.append(tool)
                if trace:
                    trace.record_tool_activated(tool_id, reason)
            else:
                if trace:
                    trace.record_tool_skipped(tool_id, reason)

        return selected

    async def execute_tools(
        self,
        tools: list[BaseTool],
        ctx: RequestContext,
        scenario: ScenarioConfig,
        trace: AgentTrace | None = None,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []

        for tool in tools:
            try:
                result = await tool.execute(ctx, scenario)
            except Exception as exc:
                logger.error("[ToolRegistry] execute error for %r: %s", tool.id, exc, exc_info=True)
                result = ToolResult(
                    tool_id=tool.id,
                    activated=False,
                    text_block=None,
                    structured_data=None,
                    reason="ошибка при выполнении tool",
                    error=str(exc)[:500],
                )

            if result.error and trace:
                trace.record_tool_error(tool.id, result.error)

            results.append(result)

        return results
