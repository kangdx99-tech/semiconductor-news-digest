"""Telegram 渠道：Bot API 发消息。

用纯文本发送并关闭 parse_mode —— 日报里带 `**`、`_`、`[` 这类字符，
开 Markdown 解析经常因为一个下标字符整条消息被拒。
"""

from __future__ import annotations

from typing import Any, Dict, List

import requests

from ..renderer import split_for_chat
from .base import DeliverResult, DeliveryContext

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000  # Telegram 上限 4096，留点余量


def deliver(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    token = ctx.env(options.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), True)
    chat_id = str(options.get("chat_id") or "").strip()
    if not chat_id:
        return DeliverResult("telegram", False, "缺少 chat_id")

    ids: List[str] = []
    for chunk in split_for_chat(ctx.plain_text, limit=MAX_LEN):
        resp = requests.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": bool(options.get("disable_preview", False)),
            },
            timeout=60,
        )
        data = resp.json()
        if not data.get("ok"):
            return DeliverResult("telegram", False, f"发送失败：{str(data)[:200]}")
        ids.append(str(data["result"]["message_id"]))
        if ctx.verbose:
            print(f"  [Telegram] 已发送 message_id={ids[-1]}")

    return DeliverResult("telegram", True, f"发送 {len(ids)} 条消息", ids)
