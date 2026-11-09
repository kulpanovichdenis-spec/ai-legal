"""
SkillRegistry — формальный выбор skills через scenario_id, activation_condition
и priority (Этап 1).

Совместимость со старой таблицей skills:
  - если scenario_ids IS NULL → legacy mapping через mode поле:
      mode='all'          → кандидат для всех scenarios (пометить как legacy)
      mode=<legacy_mode>  → кандидат только если scenario.legacy_mode_group совпадает
  - если scenario_ids заполнен → использовать новый маппинг.

activation_condition поддерживает:
  - 'always'          → всегда активен
  - 'keyword:w1|w2'   → активен если слова найдены в message
  - 'disabled'        → никогда не активен
"""

from __future__ import annotations

import json
import logging
import re

import aiosqlite

from schemas_agent import MemoryContext, RequestContext, ScenarioConfig, SelectedSkill, SkillConfig
from agent_trace import AgentTrace

logger = logging.getLogger("legal-assistant")

# Скилл с mode=all и scenario_ids=NULL крупнее этого порога — подозрительно:
# скорее всего, он предназначен для одного сценария, но не настроен через scenario_ids.
_LEGACY_ALL_WARN_CHARS = 5_000

# Мягкий лимит суммарного содержимого скиллов, добавляемых в промпт за один запрос.
# Скиллы с явным scenario_ids всегда проходят; legacy mode=all обрезаются, если
# суммарный объём превышает лимит (сортировка по priority — меньший приоритет обрезается первым).
_MAX_SKILLS_TOTAL_CHARS = 20_000

# Миграция колонок skills вынесена в migrations.py (m003_agentic_columns).
# Раньше здесь был upgrade_skills_table(), который запускался на КАЖДЫЙ
# select_skills — лишний PRAGMA + потенциальный ALTER в hot-path запроса.

# Сколько последних сообщений истории подмешивать в keyword-проверку помимо
# текущего. Без этого короткий ответ на уточняющий вопрос скилла (например,
# однословный ответ "ЛС" на "что жертвуем?") не содержит триггер-слов сам по
# себе и скилл выпадает из промпта на следующем же шаге диалога.
_ACTIVATION_HISTORY_WINDOW = 2


def _check_activation(condition: str | None, message: str) -> tuple[bool, str]:
    """Вернуть (активировать, reason)."""
    cond = (condition or "always").strip().lower()

    if cond == "disabled":
        return False, "condition=disabled"

    if cond == "always":
        return True, "condition=always"

    if cond.startswith("keyword:"):
        words = cond[len("keyword:"):].split("|")
        msg_lower = message.lower()
        for w in words:
            w = w.strip()
            if w and re.search(re.escape(w), msg_lower):
                return True, f"keyword match: {w!r}"
        return False, f"no keyword match for: {words}"

    logger.warning("[SkillRegistry] unknown activation_condition=%r, treating as always", cond)
    return True, f"unknown condition={cond!r}, treating as always"


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: plain comma-separated
    return [x.strip() for x in value.split(",") if x.strip()]


class SkillRegistry:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def select_skills(
        self,
        scenario: ScenarioConfig,
        ctx: RequestContext,
        trace: AgentTrace | None = None,
        memory: MemoryContext | None = None,
    ) -> list[SelectedSkill]:
        try:
            async with aiosqlite.connect(self._db_path, timeout=10.0) as db:
                db.row_factory = aiosqlite.Row
                # Колонки skills гарантированы миграциями (migrations.py) на старте.
                async with db.execute(
                    """
                    SELECT id, name, content, mode, is_active,
                           scenario_ids, activation_condition,
                           priority, required, exclusive_with, depends_on
                    FROM skills
                    WHERE is_active = 1
                    ORDER BY priority ASC
                    """
                ) as cur:
                    rows = await cur.fetchall()
        except Exception as exc:
            logger.error("[SkillRegistry] DB error: %s", exc)
            return []

        candidates: list[dict] = []

        # keyword-проверка идёт не только по текущему сообщению, но и по
        # последним нескольким репликам диалога (см. _ACTIVATION_HISTORY_WINDOW) —
        # чтобы короткий ответ на уточняющий вопрос скилла не терял его контекст.
        activation_text = ctx.message or ""
        if memory and memory.messages:
            recent = memory.messages[-_ACTIVATION_HISTORY_WINDOW:]
            activation_text = " ".join(m.get("content", "") for m in recent) + " " + activation_text

        for row in rows:
            skill_id = str(row["id"])
            skill_name = row["name"]
            skill_mode = row["mode"] or ""
            scenario_ids_raw = row["scenario_ids"]
            scenario_ids = _parse_json_list(scenario_ids_raw)
            activation_cond = row["activation_condition"] or "always"
            priority = row["priority"] if row["priority"] is not None else 100

            # ── Определяем, кандидат ли skill для этого scenario ─────────────
            matched = False
            source = "db"
            reason_match = ""

            if scenario_ids:
                # Новый mapping: явный список scenario_ids
                if scenario.id in scenario_ids:
                    matched = True
                    source = "scenario_ids"
                    reason_match = f"scenario_ids contains {scenario.id!r}"
            else:
                # Legacy mapping через mode
                if skill_mode == "all":
                    matched = True
                    source = "legacy_mode_all"
                    reason_match = "legacy mode=all (все сценарии)"
                elif skill_mode == scenario.legacy_mode_group:
                    matched = True
                    source = "legacy_mode_specific"
                    reason_match = f"legacy mode={skill_mode!r} == {scenario.legacy_mode_group!r}"

            if not matched:
                if trace:
                    trace.record_skill_skipped(
                        skill_id, skill_name,
                        f"not in scope for scenario {scenario.id!r} (mode={skill_mode!r})"
                    )
                continue

            # Обязательный скилл сценария не зависит от ключевых слов в тексте.
            # Иначе «Навигатор по типовым формам» молчит, когда пользователь
            # нажал «типовые формы» и написал только «нужен договор поставки».
            if skill_name in scenario.required_skill_ids:
                activated, act_reason = True, "required skill for scenario"
            else:
                activated, act_reason = _check_activation(activation_cond, activation_text)
            if not activated:
                if trace:
                    trace.record_skill_skipped(skill_id, skill_name, act_reason)
                continue

            candidates.append({
                "id": skill_id,
                "name": skill_name,
                "content": row["content"] or "",
                "priority": priority,
                "source": source,
                "reason": f"{reason_match}; {act_reason}",
                "exclusive_with": _parse_json_list(row["exclusive_with"]),
                "depends_on": _parse_json_list(row["depends_on"]),
                "required": bool(row["required"]),
            })

        # ── Предупреждение о крупных legacy mode=all скиллах ─────────────────
        for c in candidates:
            if c["source"] == "legacy_mode_all" and len(c["content"]) > _LEGACY_ALL_WARN_CHARS:
                logger.warning(
                    "[SkillRegistry] КРУПНЫЙ legacy скилл без scenario_ids: "
                    "skill=%r content=%d chars, scenario=%s. "
                    "Назначьте scenario_ids в AdminSkills чтобы убрать загрязнение промпта.",
                    c["name"], len(c["content"]), scenario.id,
                )

        # ── Сортировка по priority ────────────────────────────────────────────
        candidates.sort(key=lambda x: x["priority"])

        # ── Обработка exclusive_with + бюджет символов ────────────────────────
        selected_ids: set[str] = set()
        result: list[SelectedSkill] = []
        total_chars = 0

        for c in candidates:
            # Проверяем, не исключён ли текущий skill уже выбранным
            excluded_by = [sid for sid in c["exclusive_with"] if sid in selected_ids]
            if excluded_by:
                if trace:
                    trace.record_skill_skipped(
                        c["id"], c["name"],
                        f"exclusive_with conflict: {excluded_by}"
                    )
                continue

            # Бюджет: legacy mode=all скиллы не проходят при превышении лимита.
            # Скиллы с явным scenario_ids (source != legacy_mode_all) всегда добавляются.
            content_len = len(c["content"])
            if c["source"] == "legacy_mode_all" and total_chars + content_len > _MAX_SKILLS_TOTAL_CHARS:
                logger.warning(
                    "[SkillRegistry] бюджет скиллов (%d chars) исчерпан — пропускаем "
                    "legacy скилл %r (%d chars). Назначьте scenario_ids для явного включения.",
                    _MAX_SKILLS_TOTAL_CHARS, c["name"], content_len,
                )
                if trace:
                    trace.record_skill_skipped(
                        c["id"], c["name"],
                        f"budget exceeded: total={total_chars} + {content_len} > {_MAX_SKILLS_TOTAL_CHARS}",
                    )
                continue

            total_chars += content_len
            selected_ids.add(c["id"])
            skill = SelectedSkill(
                id=c["id"],
                name=c["name"],
                content=c["content"],
                priority=c["priority"],
                reason=c["reason"],
            )
            result.append(skill)

            if trace:
                trace.record_skill_activated(c["id"], c["name"], c["reason"])

        if trace and candidates:
            logger.info(
                "[SkillRegistry] scenario=%s: %d candidates, %d selected (%d chars total), %d skipped",
                scenario.id, len(candidates), len(result), total_chars,
                len(candidates) - len(result),
            )

        return result
