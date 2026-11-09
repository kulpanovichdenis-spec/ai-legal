"""
Tests: WebAdapter + Channel normalization

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_channel_adapters.py -v
"""

import pytest
from channels.web_adapter import WebAdapter
from channels.base import InboundMessage


def test_web_adapter_normalize_inbound():
    inbound = WebAdapter.normalize_inbound(
        user_id="user-123",
        message="Проверь договор",
        conversation_id=None,
        attachment_names=["doc.pdf"],
    )
    assert isinstance(inbound, InboundMessage)
    assert inbound.channel == "web"
    assert inbound.user_id == "user-123"
    assert inbound.text == "Проверь договор"
    assert inbound.external_user_id is None
    assert "doc.pdf" in inbound.attachments


def test_web_adapter_to_request_context():
    inbound = WebAdapter.normalize_inbound(user_id="u1", message="тест")
    ctx = WebAdapter.to_request_context(
        inbound,
        conversation_id="conv-1",
        legacy_mode="auto",
        file_context="file text",
    )
    assert ctx.channel == "web"
    assert ctx.user_id == "u1"
    assert ctx.message == "тест"
    assert ctx.conversation_id == "conv-1"
    assert ctx.file_context == "file text"
    assert ctx.request_id  # не пустой UUID


def test_web_request_context_has_all_fields():
    inbound = WebAdapter.normalize_inbound(user_id="u2", message="вопрос")
    ctx = WebAdapter.to_request_context(inbound)
    assert hasattr(ctx, "channel")
    assert hasattr(ctx, "external_user_id")
    assert hasattr(ctx, "external_chat_id")
    assert hasattr(ctx, "channel_metadata")
    assert ctx.external_user_id is None
    assert ctx.channel_metadata == {}
