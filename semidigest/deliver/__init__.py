"""推送渠道。每个渠道实现 deliver(markdown, plain_text, ctx) -> DeliverResult。"""

from __future__ import annotations

from .base import DeliverResult, DeliveryContext, deliver_all, get_channel

__all__ = ["DeliverResult", "DeliveryContext", "deliver_all", "get_channel"]
