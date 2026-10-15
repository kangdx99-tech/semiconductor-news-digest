"""本地 markdown 渠道：把日报存成文件，永远可用的兜底出口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .base import DeliverResult, DeliveryContext


def deliver(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    out_dir = ctx.base_dir / options.get("dir", "./output")
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = options.get("filename", "{date}.md")
    filename = pattern.format(date=ctx.date_str, title=ctx.digest_title)
    path = out_dir / filename

    body = f"{ctx.markdown}\n"
    path.write_text(body, encoding="utf-8")

    # 可选：同时按日期归档一份，方便历史回溯
    if options.get("keep_history", False):
        archive = out_dir / "history"
        archive.mkdir(parents=True, exist_ok=True)
        stamp = ctx.extra.get("timestamp") or ctx.date_str
        (archive / f"{stamp}.md").write_text(body, encoding="utf-8")

    return DeliverResult("local_md", True, f"{path}", [str(path)])
