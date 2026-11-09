"""
Orchestrator — центральный слой подготовки ExecutionPlan (Этап 1).

Не вызывает LLM сам.
Не занимается HTTP.
Не зависит напрямую от FastAPI.

Порядок:
  1. Router → RouterDecision
  2. FallbackPolicy.resolve()
  3. ScenarioRegistry.get()
  4. PrivacyManager.get_policy()
  5. MemoryManager.load()
  6. SkillRegistry.select_skills()
  7. ToolRegistry.select_tools()  (только selection, не execute)
  8. AgentTrace
  9. ExecutionPlan
"""

from __future__ import annotations

import logging
import uuid

from schemas_agent import (
    RequestContext, RouterDecision, ExecutionPlan, ToolResult,
)
from tools.base import ToolPhase
from scenario_registry import ScenarioRegistry
from skill_registry import SkillRegistry
from tool_registry import ToolRegistry
from prompt_registry import PromptRegistry
from prompt_assembler import PromptAssembler
from memory_manager import MemoryManager
from privacy_manager import PrivacyManager
from fallback_policy import FallbackPolicy
from agent_trace import AgentTrace

logger = logging.getLogger("legal-assistant")


class Orchestrator:
    def __init__(
        self,
        db_path: str,
        scenario_registry: ScenarioRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        prompt_registry: PromptRegistry | None = None,
        memory_manager: MemoryManager | None = None,
        privacy_manager: PrivacyManager | None = None,
        fallback_policy: FallbackPolicy | None = None,
    ) -> None:
        self._db_path = db_path
        self._scenario_registry = scenario_registry or ScenarioRegistry.get_instance()
        self._skill_registry = skill_registry or SkillRegistry(db_path)
        self._tool_registry = tool_registry or ToolRegistry(db_path)
        self._prompt_registry = prompt_registry or PromptRegistry(db_path)
        self._memory_manager = memory_manager or MemoryManager(db_path)
        self._privacy_manager = privacy_manager or PrivacyManager()
        self._fallback_policy = fallback_policy or FallbackPolicy()
        self._prompt_assembler = PromptAssembler(self._prompt_registry)

    async def build_plan(
        self,
        ctx: RequestContext,
        router_decision: RouterDecision | None = None,
    ) -> ExecutionPlan:
        trace = AgentTrace(request_id=ctx.request_id)
        trace.start_timer("orchestrate")

        # ── 1. Router (если decision не передан — вызываем сами) ─────────────
        if router_decision is None:
            router_decision = await self._call_router(ctx, trace)

        # ── 2. FallbackPolicy ──────────────────────────────────────────────────
        trace.start_timer("fallback")
        router_decision = self._fallback_policy.resolve(router_decision)
        trace.stop_timer("fallback")

        if router_decision.clarification:
            trace.clarification_sent = True

        # ── 3. ScenarioConfig ──────────────────────────────────────────────────
        scenario_id = router_decision.scenario_id or "legal_consultation"
        scenario = self._scenario_registry.get(scenario_id)

        trace.scenario_id = scenario.id
        trace.legacy_mode = scenario.legacy_mode_group
        trace.scenario_confidence = router_decision.confidence
        trace.scenario_reason = router_decision.reason
        trace.intent_id = router_decision.intent.id if router_decision.intent else None
        trace.fallback_used = router_decision.confidence < 0.5

        logger.info(
            "[Orchestrator] request_id=%s scenario=%s legacy=%s conf=%.2f",
            ctx.request_id, scenario.id, scenario.legacy_mode_group,
            router_decision.confidence,
        )

        # ── 4. PrivacyPolicy ───────────────────────────────────────────────────
        privacy_policy = self._privacy_manager.get_policy(scenario.privacy_policy_id)
        trace.privacy_policy_id = privacy_policy.id

        # ── 5. MemoryContext ───────────────────────────────────────────────────
        trace.start_timer("memory")
        memory = await self._memory_manager.load(
            ctx.conversation_id,
            max_messages=scenario.max_history_messages,
        )
        ctx.metadata["dialog_history"] = [
            {"role": m.get("role", ""), "content": m.get("content") or ""}
            for m in memory.messages
        ]
        trace.stop_timer("memory")
        trace.history_messages_used = memory.messages_used
        trace.history_truncated = memory.truncated

        # ── 6. SkillRegistry ───────────────────────────────────────────────────
        trace.start_timer("skills")
        skills = await self._skill_registry.select_skills(scenario, ctx, trace, memory=memory)
        trace.stop_timer("skills")

        # ── 7. ToolRegistry (selection only) ──────────────────────────────────
        trace.start_timer("tools_select")
        selected_tools = await self._tool_registry.select_tools(scenario, ctx, trace)
        tool_ids = [t.id for t in selected_tools]
        trace.stop_timer("tools_select")

        trace.stop_timer("orchestrate")

        return ExecutionPlan(
            request_context=ctx,
            router_decision=router_decision,
            scenario=scenario,
            skills=skills,
            tool_ids=tool_ids,
            memory=memory,
            privacy_policy=privacy_policy,
            prompt_id=scenario.prompt_id,
            legacy_mode=scenario.legacy_mode_group,
            trace=trace,
        )

    async def _call_router(
        self,
        ctx: RequestContext,
        trace: AgentTrace,
    ) -> RouterDecision:
        trace.start_timer("router")
        try:
            import router_agent as _router
            history_tail = (ctx.metadata or {}).get("history_tail", [])
            legacy_decision = await _router.route(
                message=ctx.message or "",
                attachment_names=ctx.attachments or [],
                history_tail=history_tail,
            )

            # Конвертируем старый RouterDecision в новый
            scenario_id = None
            if legacy_decision.mode and legacy_decision.mode not in ("null", ""):
                from scenario_registry import ScenarioRegistry
                scenario_id = ScenarioRegistry.get_instance().from_legacy_mode(
                    legacy_decision.mode
                ).id

            decision = RouterDecision(
                scenario_id=scenario_id,
                legacy_mode=legacy_decision.mode,
                intent=None,
                confidence=legacy_decision.confidence,
                reason=legacy_decision.reason,
                clarification=legacy_decision.clarification,
                alternatives=[],
            )
        except Exception as exc:
            logger.error("[Orchestrator] router error: %s", exc, exc_info=True)
            decision = None
            decision = self._fallback_policy.resolve(None, error=exc)
            trace.fallback_used = True
            trace.fallback_reason = str(exc)[:200]
        finally:
            trace.stop_timer("router")

        return decision

    def get_tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    async def execute_tools_by_phase(
        self,
        plan: ExecutionPlan,
        phase: ToolPhase,
    ) -> list[ToolResult]:
        """Выполнить только tools нужной фазы из ExecutionPlan.

        Заменяет ручное копание в `_tool_registry._tools` из main.py.
        Если в plan нет инструментов этой фазы — возвращает пустой список.
        """
        tools = [self._tool_registry.get(tid) for tid in plan.tool_ids]
        tools = [t for t in tools if t is not None]
        tools = self._tool_registry.filter_by_phase(tools, phase)
        if not tools:
            return []
        return await self._tool_registry.execute_tools(
            tools, plan.request_context, plan.scenario, plan.trace,
        )

    def get_prompt_assembler(self) -> PromptAssembler:
        return self._prompt_assembler

    def get_privacy_manager(self) -> PrivacyManager:
        return self._privacy_manager

    def get_skill_registry(self) -> SkillRegistry:
        return self._skill_registry

    def get_prompt_registry(self) -> PromptRegistry:
        return self._prompt_registry
