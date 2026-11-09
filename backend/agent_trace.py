"""
AgentTrace — логирование решений агентского pipeline (Этап 1).

Trace создаётся в начале обработки запроса и заполняется по мере прохождения
pipeline. По завершению:
  - компактный вариант добавляется в SSE meta event;
  - полный вариант логируется server-side;
  - PII не хранятся.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("legal-assistant")


@dataclass
class AgentTrace:
    request_id: str
    scenario_id: str | None = None
    legacy_mode: str | None = None
    scenario_confidence: float | None = None
    scenario_reason: str | None = None
    intent_id: str | None = None
    clarification_sent: bool = False
    skills_activated: list[dict] = field(default_factory=list)
    skills_skipped: list[dict] = field(default_factory=list)
    tools_activated: list[dict] = field(default_factory=list)
    tools_skipped: list[dict] = field(default_factory=list)
    rag_query: str | None = None
    rag_docs_count: int | None = None
    history_messages_used: int | None = None
    history_truncated: bool | None = None
    privacy_policy_id: str | None = None
    llm_model: str | None = None
    timing: dict[str, float] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None

    # ── Timers ────────────────────────────────────────────────────────────────

    def start_timer(self, key: str) -> None:
        self.timing[f"_start_{key}"] = time.monotonic()

    def stop_timer(self, key: str) -> None:
        start = self.timing.pop(f"_start_{key}", None)
        if start is not None:
            self.timing[key] = round(time.monotonic() - start, 3)

    # ── Skill recording ───────────────────────────────────────────────────────

    def record_skill_activated(self, skill_id: str, name: str, reason: str) -> None:
        self.skills_activated.append({"id": skill_id, "name": name, "reason": reason})

    def record_skill_skipped(self, skill_id: str, name: str, reason: str) -> None:
        self.skills_skipped.append({"id": skill_id, "name": name, "reason": reason})

    # ── Tool recording ────────────────────────────────────────────────────────

    def record_tool_activated(self, tool_id: str, reason: str) -> None:
        self.tools_activated.append({"id": tool_id, "reason": reason})

    def record_tool_skipped(self, tool_id: str, reason: str) -> None:
        self.tools_skipped.append({"id": tool_id, "reason": reason})

    def record_tool_error(self, tool_id: str, error: str) -> None:
        for t in self.tools_activated:
            if t["id"] == tool_id:
                t["error"] = error[:200]
                return
        self.tools_activated.append({"id": tool_id, "error": error[:200]})

    # ── Output ────────────────────────────────────────────────────────────────

    def to_meta_event(self) -> dict:
        """Компактный вариант для SSE meta event (нет PII)."""
        return {
            "request_id": self.request_id,
            "scenario_id": self.scenario_id,
            "legacy_mode": self.legacy_mode,
            "confidence": self.scenario_confidence,
            "skills": len(self.skills_activated),
            "tools": len(self.tools_activated),
            "rag_docs": self.rag_docs_count,
            "history_used": self.history_messages_used,
            "truncated": self.history_truncated,
            "fallback": self.fallback_used,
            "timing": {k: v for k, v in self.timing.items() if not k.startswith("_")},
        }

    def to_log(self) -> dict:
        """Полный вариант для server-side логирования."""
        return {
            "request_id": self.request_id,
            "scenario_id": self.scenario_id,
            "legacy_mode": self.legacy_mode,
            "scenario_confidence": self.scenario_confidence,
            "scenario_reason": self.scenario_reason,
            "intent_id": self.intent_id,
            "clarification_sent": self.clarification_sent,
            "skills_activated": self.skills_activated,
            "skills_skipped": self.skills_skipped,
            "tools_activated": self.tools_activated,
            "tools_skipped": self.tools_skipped,
            "rag_docs_count": self.rag_docs_count,
            "history_messages_used": self.history_messages_used,
            "history_truncated": self.history_truncated,
            "privacy_policy_id": self.privacy_policy_id,
            "llm_model": self.llm_model,
            "timing": {k: v for k, v in self.timing.items() if not k.startswith("_")},
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }

    def log(self) -> None:
        logger.info("[AgentTrace] %s", self.to_log())
