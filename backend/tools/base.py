"""BaseTool — абстрактный базовый класс для всех tools (Этап 1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from schemas_agent import RequestContext, ScenarioConfig, ToolResult


# Фаза исполнения инструмента в пайплайне /api/chat:
#   - "pre_rag":  до RAG-поиска; результат может изменить запрос/документы (product_lookup)
#   - "prompt":   после RAG, перед сборкой prompt; результат идёт в блок «РЕЗУЛЬТАТЫ ИНСТРУМЕНТОВ»
ToolPhase = Literal["pre_rag", "prompt"]


class BaseTool(ABC):
    id: str
    # Фаза по умолчанию — prompt. Pre-RAG инструменты выставляют явно.
    phase: ToolPhase = "prompt"

    @abstractmethod
    async def should_activate(
        self,
        ctx: "RequestContext",
        scenario: "ScenarioConfig",
    ) -> tuple[bool, str]:
        """Вернуть (активировать, reason)."""
        ...

    @abstractmethod
    async def execute(
        self,
        ctx: "RequestContext",
        scenario: "ScenarioConfig",
    ) -> "ToolResult":
        """Выполнить tool и вернуть ToolResult.

        При ошибке НЕ бросать исключение — вернуть ToolResult(error=...).
        """
        ...
