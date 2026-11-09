"""
Контракты агентской архитектуры (Этап 1).

Все dataclass-ы намеренно просты: никаких ORM, никаких Pydantic-зависимостей.
Pydantic-модели для HTTP-слоя остаются в main.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Intent ────────────────────────────────────────────────────────────────────

INTENT_IDS = (
    "ask_legal_question",
    "check_ad",
    "analyze_contract",
    "draft_claim",
    "draft_notice",
    "prepare_document_from_contract",
    "navigate_template_forms",
    "consumer_return_or_replacement",
    "lookup_regulation",
)


@dataclass
class Intent:
    id: str
    confidence: float
    signals: list[str] = field(default_factory=list)


# ── RouterDecision ────────────────────────────────────────────────────────────

@dataclass
class RouterDecision:
    scenario_id: str | None
    legacy_mode: str | None
    intent: Intent | None
    confidence: float
    reason: str
    clarification: str | None = None
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "legacy_mode": self.legacy_mode,
            "intent": {
                "id": self.intent.id,
                "confidence": self.intent.confidence,
                "signals": self.intent.signals,
            } if self.intent else None,
            "confidence": self.confidence,
            "reason": self.reason,
            "clarification": self.clarification,
            "alternatives": self.alternatives,
        }


# ── ScenarioConfig ────────────────────────────────────────────────────────────

@dataclass
class ScenarioConfig:
    id: str
    label: str
    legacy_mode_group: str
    prompt_id: str
    required_skill_ids: list[str]
    optional_skill_ids: list[str]
    allowed_tool_ids: list[str]
    rag_strategy: str
    privacy_policy_id: str
    memory_policy_id: str
    model_policy_id: str | None
    timeout_s: int | None
    max_history_messages: int
    enabled: bool = True


# ── SkillConfig ───────────────────────────────────────────────────────────────

@dataclass
class SkillConfig:
    id: str
    name: str
    content: str
    scenario_ids: list[str]
    activation_condition: str   # "always" | "keyword:<w1>|<w2>" | "disabled"
    priority: int
    required: bool
    exclusive_with: list[str]
    depends_on: list[str]
    source: str                 # "db" | "legacy_mode_all" | "legacy_mode_specific"


# ── SelectedSkill ─────────────────────────────────────────────────────────────

@dataclass
class SelectedSkill:
    id: str
    name: str
    content: str
    priority: int
    reason: str


# ── ToolResult ────────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    tool_id: str
    activated: bool
    text_block: str | None
    structured_data: dict[str, Any] | None
    reason: str
    error: str | None = None


# ── MemoryContext ─────────────────────────────────────────────────────────────

@dataclass
class MemoryContext:
    messages: list[dict[str, str]]
    summary: str | None
    total_messages_in_db: int
    messages_used: int
    truncated: bool


# ── PrivacyPolicy ─────────────────────────────────────────────────────────────

@dataclass
class PrivacyPolicy:
    id: str
    anonymize_user_message: bool
    anonymize_file_context: bool
    anonymize_rag_context: bool
    anonymize_skill_content: bool
    anonymize_tool_results: bool
    deanonymize_response: bool
    allow_raw_for_tools: list[str]  # tool ids allowed to receive raw (non-anonymized) data
    store_raw_messages: bool
    store_anonymized_messages: bool


# ── AgentTrace (forward-ref) ──────────────────────────────────────────────────
# Полное определение в agent_trace.py; здесь только аннотация.

# ── ExecutionPlan ─────────────────────────────────────────────────────────────

@dataclass
class RequestContext:
    request_id: str
    user_id: str
    message: str
    conversation_id: str | None
    legacy_mode: str | None
    channel: str                         # "web" | "bitrix24" | "api"
    external_user_id: str | None
    external_chat_id: str | None
    external_message_id: str | None
    channel_metadata: dict[str, Any]
    attachments: list[str]
    file_context: str | None
    raw_file_context: str | None
    history_summary: str | None
    metadata: dict[str, Any]


@dataclass
class ExecutionPlan:
    request_context: RequestContext
    router_decision: RouterDecision
    scenario: ScenarioConfig
    skills: list[SelectedSkill]
    tool_ids: list[str]
    memory: MemoryContext | None
    privacy_policy: PrivacyPolicy
    prompt_id: str
    legacy_mode: str
    trace: Any  # AgentTrace — circular ref avoided


# ── AgentPipelineResult ───────────────────────────────────────────────────────

@dataclass
class AgentPipelineResult:
    conversation_id: str | None
    scenario_id: str | None
    legacy_mode: str | None
    full_text: str
    structured_data: dict[str, Any] | None
    trace: Any  # AgentTrace
    stream_events: list[dict] = field(default_factory=list)
