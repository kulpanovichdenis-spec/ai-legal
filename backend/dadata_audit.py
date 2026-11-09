import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

logger = logging.getLogger("legal-assistant.dadata_audit")


@dataclass
class ExternalCheckLog:
    provider: Literal["dadata_mcp"] = "dadata_mcp"
    intent: str = ""
    timestamp: str = ""
    input_types: list[str] = field(default_factory=list)
    status: Literal["success", "not_found", "error", "skipped"] = "skipped"
    reason: str | None = None
    correlation_id: str | None = None


def log_dadata_check(
    intent: str,
    input_types: list[str],
    status: Literal["success", "not_found", "error", "skipped"],
    reason: str = "",
    correlation_id: str = "",
) -> None:
    # Не логируем: API_KEY, SECRET_KEY, authorization header, текст договора, raw response
    log = ExternalCheckLog(
        intent=intent,
        timestamp=datetime.now(timezone.utc).isoformat(),
        input_types=input_types,
        status=status,
        reason=reason or None,
        correlation_id=correlation_id or None,
    )
    logger.info(
        "[DADATA_AUDIT] provider=%s intent=%s input_types=%s status=%s reason=%s correlation_id=%s",
        log.provider,
        log.intent,
        log.input_types,
        log.status,
        log.reason,
        log.correlation_id,
    )
