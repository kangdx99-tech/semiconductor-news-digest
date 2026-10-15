"""推送渠道基类与分发逻辑。"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..renderer import to_plain_text


@dataclass
class DeliverResult:
    channel: str
    ok: bool
    detail: str = ""
    artifacts: List[str] = field(default_factory=list)

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        return f"  {mark} {self.channel:12s} {self.detail}"


@dataclass
class DeliveryContext:
    """一次运行的上下文，渠道从这里取需要的东西。"""

    markdown: str
    date_str: str
    digest_title: str
    base_dir: Path
    env: Callable[[str, bool], str]
    dry_run: bool = False
    verbose: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def plain_text(self) -> str:
        return to_plain_text(self.markdown)


# ---------------------------------------------------------------- 渠道实现
def _local_md(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    from .local_md import deliver as impl

    return impl(options, ctx)


def _feishu(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    from .feishu import deliver as impl

    return impl(options, ctx)


def _telegram(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    from .telegram import deliver as impl

    return impl(options, ctx)


def _email(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    from .email_smtp import deliver as impl

    return impl(options, ctx)


CHANNELS: Dict[str, Callable[[Dict[str, Any], DeliveryContext], DeliverResult]] = {
    "local_md": _local_md,
    "feishu": _feishu,
    "feishu_doc": _feishu,
    "telegram": _telegram,
    "email": _email,
}


def get_channel(name: str):
    key = (name or "").strip().lower()
    if key not in CHANNELS:
        raise KeyError(f"未知推送渠道：{name}（可用：{', '.join(sorted(set(CHANNELS)))}）")
    return CHANNELS[key]


def deliver_all(
    deliveries: Sequence[Dict[str, Any]], ctx: DeliveryContext
) -> List[DeliverResult]:
    """依次推送。单个渠道失败不会中断其它渠道 —— 日报生成一次很贵，能送出去多少算多少。"""
    results: List[DeliverResult] = []
    for options in deliveries:
        name = options.get("type", "")
        try:
            channel = get_channel(name)
        except KeyError as exc:
            results.append(DeliverResult(name or "?", False, str(exc)))
            continue
        if ctx.dry_run and (options.get("type", "").lower() != "local_md"):
            results.append(DeliverResult(name, True, "dry-run 跳过实际推送"))
            continue
        try:
            results.append(channel(options, ctx))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[deliver] {name} 失败：{exc}\n")
            if ctx.verbose:
                traceback.print_exc()
            results.append(DeliverResult(name, False, f"{type(exc).__name__}: {exc}"))
    return results
