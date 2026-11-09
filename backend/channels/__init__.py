"""Channels package — канально-независимый слой доступа к агентскому pipeline."""

from channels.base import InboundMessage, ChannelResponse
from channels.web_adapter import WebAdapter
from channels.bitrix24_adapter import Bitrix24Adapter

__all__ = ["InboundMessage", "ChannelResponse", "WebAdapter", "Bitrix24Adapter"]
