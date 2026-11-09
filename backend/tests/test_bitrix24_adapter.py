"""
Tests: Bitrix24Adapter

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_bitrix24_adapter.py -v
"""

import pytest
from channels.bitrix24_adapter import Bitrix24Adapter, _parse_command
from bitrix24.auth import verify_webhook_token


# ── _parse_command ─────────────────────────────────────────────────────────────

def test_parse_command_konsultaciya():
    hint, text = _parse_command("/консультация Как по ГК РФ?")
    assert hint == "legal_consultation"
    assert text == "Как по ГК РФ?"


def test_parse_command_reklama():
    hint, text = _parse_command("/реклама проверь макет")
    assert hint == "ad_check_general"
    assert text == "проверь макет"


def test_parse_command_dokument():
    hint, text = _parse_command("/документ")
    assert hint == "contract_analysis"


def test_parse_command_regla():
    hint, text = _parse_command("/регламент что-то спросить")
    assert hint == "regulation_lookup"


def test_parse_no_command():
    hint, text = _parse_command("просто вопрос без команды")
    assert hint is None
    assert text == "просто вопрос без команды"


def test_parse_command_with_empty_text():
    hint, text = _parse_command("/реклама")
    assert hint == "ad_check_general"
    assert text == ""


# ── Bitrix24Adapter.normalize_inbound ─────────────────────────────────────────

def test_normalize_inbound_basic():
    payload = {
        "data": {
            "USER_ID": "42",
            "DIALOG_ID": "chat123",
            "MESSAGE_ID": "msg-1",
            "PARAMS": {"MESSAGE": "Привет бот"},
        }
    }
    inbound = Bitrix24Adapter.normalize_inbound(payload)
    assert inbound.channel == "bitrix24"
    assert inbound.external_user_id == "42"
    assert inbound.external_chat_id == "chat123"
    assert inbound.text == "Привет бот"
    assert inbound.user_id is None  # internal user id не заполнен на этапе 1


def test_normalize_inbound_empty_payload():
    inbound = Bitrix24Adapter.normalize_inbound({})
    assert inbound.channel == "bitrix24"
    assert inbound.text == ""


def test_normalize_inbound_with_command():
    payload = {
        "data": {
            "USER_ID": "10",
            "DIALOG_ID": "d1",
            "PARAMS": {"MESSAGE": "/реклама проверь макет"},
        }
    }
    inbound = Bitrix24Adapter.normalize_inbound(payload)
    assert inbound.text == "/реклама проверь макет"


# ── Bitrix24Adapter.to_request_context ────────────────────────────────────────

def test_to_request_context_with_command():
    from channels.base import InboundMessage
    inbound = InboundMessage(
        channel="bitrix24",
        user_id=None,
        external_user_id="42",
        external_chat_id="chat1",
        external_message_id=None,
        text="/консультация вопрос по договору",
    )
    ctx, hint = Bitrix24Adapter.to_request_context(inbound)
    assert hint == "legal_consultation"
    assert ctx.channel == "bitrix24"
    assert "вопрос по договору" in ctx.message or ctx.message == "вопрос по договору"
    assert ctx.legacy_mode is None  # Bitrix24 не использует legacy mode
    assert ctx.metadata.get("scenario_hint") == "legal_consultation"


def test_to_request_context_no_command():
    from channels.base import InboundMessage
    inbound = InboundMessage(
        channel="bitrix24",
        user_id=None,
        external_user_id="5",
        external_chat_id="chat2",
        external_message_id=None,
        text="обычный вопрос без команды",
    )
    ctx, hint = Bitrix24Adapter.to_request_context(inbound)
    assert hint is None
    assert ctx.message == "обычный вопрос без команды"


# ── Bitrix24Auth ───────────────────────────────────────────────────────────────

def test_verify_webhook_token_no_tokens_configured(monkeypatch):
    """Если токены не настроены в .env — блокировать запрос."""
    monkeypatch.setattr("bitrix24.auth._APPLICATION_TOKEN", "")
    monkeypatch.setattr("bitrix24.auth._WEBHOOK_SECRET", "")
    result = verify_webhook_token(application_token=None, webhook_secret=None)
    assert result is False


def test_verify_webhook_token_correct(monkeypatch):
    monkeypatch.setattr("bitrix24.auth._APPLICATION_TOKEN", "test_token_abc")
    monkeypatch.setattr("bitrix24.auth._WEBHOOK_SECRET", "")
    result = verify_webhook_token(application_token="test_token_abc")
    assert result is True


def test_verify_webhook_token_wrong(monkeypatch):
    monkeypatch.setattr("bitrix24.auth._APPLICATION_TOKEN", "test_token_abc")
    monkeypatch.setattr("bitrix24.auth._WEBHOOK_SECRET", "")
    result = verify_webhook_token(application_token="wrong_token")
    assert result is False


def test_bitrix24_does_not_use_jwt():
    """Bitrix24 endpoint не должен требовать JWT."""
    # Проверяем косвенно: RequestContext не требует JWT payload
    from channels.base import InboundMessage
    inbound = InboundMessage(
        channel="bitrix24",
        user_id=None,  # нет JWT
        external_user_id="100",
        external_chat_id="chat100",
        external_message_id=None,
        text="тест",
    )
    ctx, _ = Bitrix24Adapter.to_request_context(inbound)
    assert ctx.user_id == ""  # пустой, не JWT
