"""飞书渠道：写入云文档（插到最开头，绝不删除历史）+ 可选聊天推送。

关于「只插不删」：这类日报文档的价值在于历史积累。本模块只用 insert（index=0），
不会调用任何 delete 接口，所以多次运行只会让文档越来越厚，不会覆盖旧内容。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..feishu_blocks import (
    insert_blocks_at_top,
    markdown_to_blocks,
    read_raw_content,
    send_chat_text,
    tenant_access_token,
)
from .base import DeliverResult, DeliveryContext


def deliver(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    app_id = ctx.env(options.get("app_id_env", "FEISHU_APP_ID"), True)
    app_secret = ctx.env(options.get("app_secret_env", "FEISHU_APP_SECRET"), True)
    doc_token = str(options.get("doc_token") or "").strip()
    chat_id = str(options.get("chat_id") or "").strip()

    if not doc_token and not chat_id:
        return DeliverResult("feishu", False, "既没有 doc_token 也没有 chat_id，没地方可送")

    token = tenant_access_token(app_id, app_secret)
    details: List[str] = []
    artifacts: List[str] = []

    # ---------------------------------------------------------- 云文档
    if doc_token and options.get("write_doc", True):
        blocks = markdown_to_blocks(ctx.markdown)
        written = insert_blocks_at_top(token, doc_token, blocks, verbose=ctx.verbose)
        details.append(f"文档写入 {written} 个 block")
        artifacts.append(f"https://feishu.cn/docx/{doc_token}")

        # 写完自检：确认文档最开头就是这一期
        if options.get("verify", True):
            try:
                head = read_raw_content(token, doc_token)[:400]
                marker = str(options.get("verify_marker") or ctx.date_str)
                if marker in head:
                    details.append(f"自检通过（开头含 {marker}）")
                else:
                    details.append(f"⚠️ 自检未命中 {marker}，请人工看一眼文档开头")
            except Exception as exc:  # noqa: BLE001
                details.append(f"⚠️ 自检失败：{exc}")

    # ---------------------------------------------------------- 聊天推送
    if chat_id and options.get("push_chat", True):
        ids = send_chat_text(token, chat_id, ctx.plain_text, verbose=ctx.verbose)
        details.append(f"聊天已推送 {len(ids)} 条消息")
        artifacts.extend(ids)

    return DeliverResult("feishu", True, "；".join(details) or "完成", artifacts)
