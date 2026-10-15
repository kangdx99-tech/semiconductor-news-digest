#!/usr/bin/env python3
"""半导体新闻日报 —— 一键生成并推送。

    python run.py                     # 按 config.yaml 跑一遍
    python run.py --dry-run           # 只生成，不推送到网络渠道
    python run.py --no-llm            # 不调模型，直接排版候选新闻（零 API key 可跑）
    python run.py --self-test         # 离线自检，不联网
    python run.py --date 2026-10-15 --window-hours 72

流水线：采集 → 链接校验 → LLM 成稿 → 链接审计 → 多渠道推送
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 让 `python run.py` 在任意目录下都能 import 到包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from semidigest import __version__
from semidigest.collector import (
    Item,
    collect,
    is_aggregator,
    window_hours_for,
)
from semidigest.config import Config, ConfigError
from semidigest.deliver import DeliveryContext, deliver_all
from semidigest.renderer import render
from semidigest.summarizer import LLMError, summarize
from semidigest.verifier import verify_items

BANNER = "═" * 62


# ---------------------------------------------------------------- 时间
def local_now(tz_name: str = "Asia/Shanghai") -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:  # Windows 无 tzdata 时退回固定偏移
        return datetime.now(timezone(timedelta(hours=8)))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="半导体新闻日报生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", help="配置文件路径（默认 ./config.yaml）")
    parser.add_argument("--env", help=".env 文件路径（默认 ./.env）")
    parser.add_argument("--date", help="输出日期，格式 YYYY-MM-DD（默认今天，按配置时区）")
    parser.add_argument("--window-hours", type=int, help="采集时间窗（小时），默认 24；周一自动放宽")
    parser.add_argument("--max-items", type=int, help="日报最多几条")
    parser.add_argument("--no-llm", action="store_true", help="不调用大模型，直接排版候选条目")
    parser.add_argument("--no-verify", action="store_true", help="跳过链接校验（更快，但可能写出死链）")
    parser.add_argument("--dry-run", action="store_true", help="只生成本地文件，不推送网络渠道")
    parser.add_argument("--out-dir", help="本地输出目录（覆盖配置）")
    parser.add_argument("--list-sources", action="store_true", help="打印配置里的源后退出")
    parser.add_argument("--self-test", action="store_true", help="离线自检后退出")
    parser.add_argument("--version", action="version", version=f"semidigest {__version__}")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


# ---------------------------------------------------------------- 自检
def self_test(verbose: bool = True) -> int:
    """离线自检：解析、时间窗、去重、block 转换、链接审计。不联网。"""
    from semidigest.collector import normalize_title, parse_date, parse_feed
    from semidigest.feishu_blocks import markdown_to_blocks
    from semidigest.renderer import audit_links, ensure_title

    sample_rss = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>TSMC 2nm ramp &amp; yields</title>
        <link>https://example.com/a?utm_source=x</link>
        <pubDate>Tue, 14 Oct 2026 10:00:00 +0000</pubDate>
        <description>&lt;p&gt;TSMC said &lt;b&gt;2nm&lt;/b&gt; yields are on track.&lt;/p&gt;</description></item>
      <item><title>TSMC 2nm ramp &amp; yields</title>
        <link>https://other.com/dup</link>
        <pubDate>Tue, 14 Oct 2026 09:00:00 +0000</pubDate></item>
    </channel></rss>"""
    checks: List[Tuple[str, bool, str]] = []

    items = parse_feed(sample_rss, "test")
    checks.append(("解析 RSS", len(items) == 2, f"得到 {len(items)} 条"))
    checks.append(("HTML 摘要清洗", items and "<b>" not in items[0].summary, items[0].summary[:40] if items else ""))
    checks.append(("UTC 跟踪参数剔除", items and "utm_source" not in items[0].url, items[0].url if items else ""))
    parsed = parse_date("Tue, 14 Oct 2026 10:00:00 +0000")
    checks.append(("RFC822 时间解析", parsed is not None and parsed.year == 2026, str(parsed)))
    checks.append(
        ("标题归一化去重", normalize_title("TSMC 2nm ramp & yields") == normalize_title("TSMC 2nm ramp  yields"),
         normalize_title("TSMC 2nm ramp & yields")),
    )

    md = "**2026年10月15日 半导体新闻日报**\n\n【一句话总结】\n测试摘要。\n\n【制造与代工】\n① 测试标题\n来源：Test | 时间：2026-10-15\n正文内容。\n🔗 Test → https://example.com/a"
    blocks = markdown_to_blocks(md)
    checks.append(("markdown→block", len(blocks) >= 6, f"{len(blocks)} 个 block"))
    checks.append(("block 数量不超单片上限", len(blocks) <= 50, f"{len(blocks)}"))

    titled = ensure_title("【一句话总结】\n正文", "2026-10-15", "半导体新闻日报")
    checks.append(("补标题行", titled.startswith("**2026-10-15 半导体新闻日报**"), titled.splitlines()[0]))

    cleaned, removed = audit_links(
        "【A】\n① 合法\n🔗 X → https://example.com/a\n\n【B】\n② 编造\n🔗 Y → https://fake-hallucinated.com/zzz",
        ["https://example.com/a"],
    )
    checks.append(("链接审计剔除编造链接", "fake-hallucinated" not in cleaned and len(removed) == 1,
                   f"丢弃 {removed}"))

    ok = all(passed for _, passed, _ in checks)
    if verbose:
        print(BANNER)
        print(f"自检（离线，semidigest {__version__}）")
        print(BANNER)
        for name, passed, detail in checks:
            print(f"  {'✅' if passed else '❌'} {name:24s} {detail}")
        print(BANNER)
        print("全部通过 ✅" if ok else "存在失败项 ❌")
    return 0 if ok else 1


# ---------------------------------------------------------------- 主流程
def run(args: argparse.Namespace) -> int:
    verbose = not args.quiet
    try:
        cfg = Config.load(args.config, args.env)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.list_sources:
        print("采集源：")
        for src in cfg.sources():
            print(f"  - {src.get('name', '?'):24s} {src.get('url', '')}")
        print("关键词搜索（仅作选题提示，链接不进正文）：")
        for search in cfg.searches():
            print(f"  - {search.get('q') or search.get('query')}")
        print("推送渠道：")
        for entry in cfg.deliveries():
            print(f"  - {entry.get('type')}")
        return 0

    tz_name = cfg.get("digest.timezone", "Asia/Shanghai")
    now = local_now(tz_name)
    date_str = args.date or now.strftime("%Y-%m-%d")
    digest_title = cfg.get("digest.title", "半导体新闻日报")
    max_items = args.max_items or int(cfg.get("digest.max_items", 12))

    window_hours = args.window_hours or window_hours_for(
        now,
        default=int(cfg.get("digest.window_hours", 24)),
        monday=int(cfg.get("digest.monday_window_hours", 72)),
    )

    if verbose:
        print(BANNER)
        print(f"{digest_title} · {date_str} · 时间窗 {window_hours}h · semidigest {__version__}")
        print(BANNER)

    # ------------------------------------------------------ 1. 采集
    try:
        items = collect(
            cfg.sources(),
            cfg.searches(),
            window_hours=window_hours,
            timeout=int(cfg.get("collect.timeout", 20)),
            max_per_source=int(cfg.get("collect.max_per_source", 15)),
            workers=int(cfg.get("collect.workers", 8)),
            now=now.astimezone(timezone.utc),
            keyword_filter=cfg.get("collect.keyword_filter", []) or [],
            verbose=verbose,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"采集阶段异常：{exc}", file=sys.stderr)
        traceback.print_exc()
        return 3

    if not items:
        print("没有采集到任何候选新闻，终止（不写空日报）。", file=sys.stderr)
        return 4

    # ------------------------------------------------------ 2. 链接校验
    if args.no_verify or not cfg.flag("verify.enabled"):
        if verbose:
            print("  [校验] 已跳过（--no-verify）")
    else:
        verify_items(
            items,
            timeout=int(cfg.get("verify.timeout", 15)),
            workers=int(cfg.get("verify.workers", 12)),
            accept_403=cfg.flag("verify.accept_403"),
            retries=int(cfg.get("verify.retries", 1)),
            verbose=verbose,
        )

    linkable = [item for item in items if getattr(item, "keep", True) and not is_aggregator(item)]
    hints = [item.title for item in items if is_aggregator(item)]
    if verbose:
        print(f"  [筛选] 可引用 {len(linkable)} 条 · 搜索提示 {len(hints)} 条")
    if not linkable:
        print("校验后没有可引用的条目（全部链接不可用），终止。", file=sys.stderr)
        return 4

    # ------------------------------------------------------ 3. 成稿
    llm_cfg = dict(cfg.get("llm", {}) or {})
    try:
        llm_cfg["api_key"] = cfg.env(llm_cfg.get("api_key_env", "DEEPSEEK_API_KEY"), required=False)
    except Exception:  # noqa: BLE001
        llm_cfg["api_key"] = ""
    draft = summarize(
        linkable,
        llm_cfg=llm_cfg,
        date_str=date_str,
        digest_title=digest_title,
        window_hours=window_hours,
        max_items=max_items,
        hints=hints,
        extra_instructions=str(cfg.get("digest.extra_instructions", "")),
        no_llm=args.no_llm,
        verbose=verbose,
    )

    # ------------------------------------------------------ 4. 渲染 + 审计
    markdown, removed = render(
        draft,
        date_str=date_str,
        digest_title=digest_title,
        allowed_items=linkable,
        verbose=verbose,
    )

    # ------------------------------------------------------ 5. 推送
    deliveries = cfg.deliveries()
    if args.out_dir:
        for entry in deliveries:
            if entry.get("type") == "local_md":
                entry["dir"] = args.out_dir
        if not any(e.get("type") == "local_md" for e in deliveries):
            deliveries.append({"type": "local_md", "dir": args.out_dir})

    ctx = DeliveryContext(
        markdown=markdown,
        date_str=date_str,
        digest_title=digest_title,
        base_dir=cfg.base_dir,
        env=cfg.env,
        dry_run=args.dry_run,
        verbose=verbose,
        extra={"timestamp": now.strftime("%Y-%m-%d_%H%M%S")},
    )
    results = deliver_all(deliveries, ctx)

    if verbose:
        print(BANNER)
        print("推送结果：")
        for result in results:
            print(result.line())
        print(BANNER)

    # 运行日志，方便排查
    log_dir = cfg.base_dir / "output"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"run-{date_str}.json").write_text(
        json.dumps(
            {
                "date": date_str,
                "generated_at": now.isoformat(),
                "window_hours": window_hours,
                "collected": len(items),
                "linkable": len(linkable),
                "hints": len(hints),
                "audit_removed_links": removed,
                "deliveries": [
                    {"channel": r.channel, "ok": r.ok, "detail": r.detail} for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return 0 if all(r.ok for r in results) else 6


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test(not args.quiet)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except LLMError as exc:
        print(f"模型调用失败：{exc}", file=sys.stderr)
        return 5
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
