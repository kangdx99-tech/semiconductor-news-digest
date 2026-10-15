"""LLM 层：调用任意 OpenAI 兼容接口，把候选新闻写成日报 markdown。

同时提供一个零依赖的降级路径（no_llm=True）：不调模型，直接把候选新闻排版成
列表 —— 这样没有 API key 的人 clone 下来也能立刻看到东西，而不是一片报错。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

from .collector import Item
from .prompt import build_prompt, window_description


class LLMError(RuntimeError):
    pass


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def chat(
    messages: Sequence[Dict[str, str]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.4,
    max_tokens: int = 4000,
    timeout: int = 180,
    retries: int = 3,
    verbose: bool = True,
) -> str:
    """调用 OpenAI 兼容的 /chat/completions，返回正文文本。"""
    url = _endpoint(base_url)
    payload = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"网络错误: {exc}"
        else:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError) as exc:
                    last_error = f"响应结构异常: {exc} / {resp.text[:300]}"
                else:
                    if verbose:
                        usage = data.get("usage", {})
                        print(
                            f"  [LLM] {model} 完成，tokens in/out = "
                            f"{usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}"
                        )
                    return content
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        if attempt < retries:
            sleep_for = 3 * attempt
            print(f"  [LLM] 第 {attempt} 次失败（{last_error}），{sleep_for}s 后重试", file=sys.stderr)
            time.sleep(sleep_for)
    raise LLMError(f"调用 {url} 失败：{last_error}")


def clean_markdown(text: str) -> str:
    """去掉模型爱加的代码块围栏和寒暄开场。"""
    out = text.strip()
    out = re.sub(r"^```(?:markdown|md)?\s*", "", out)
    out = re.sub(r"\s*```$", "", out)
    out = re.sub(r"^(好的|以下是|这是)[^\n]{0,40}\n+", "", out)
    return out.strip()


def fallback_markdown(
    items: Sequence[Item],
    *,
    date_str: str,
    digest_title: str = "半导体新闻日报",
    window_hours: int = 24,
    max_items: int = 20,
) -> str:
    """不调模型时的排版：按来源分组罗列，保证工具在零 API key 下也能跑通。"""
    lines = [f"**{date_str} {digest_title}**", ""]
    lines.append("【说明】未配置 LLM（--no-llm），以下为{}抓到的原始条目，未经编辑筛选。".format(window_description(window_hours)))
    lines.append("")
    grouped: Dict[str, List[Item]] = {}
    for item in items[:max_items]:
        grouped.setdefault(item.source, []).append(item)
    for source, group in grouped.items():
        lines.append(f"【{source}】")
        for idx, item in enumerate(group, start=1):
            stamp = item.published.astimezone().strftime("%Y-%m-%d %H:%M") if item.published else "时间未知"
            lines.append(f"{idx}. {item.title}")
            lines.append(f"   时间：{stamp}")
            if item.summary:
                lines.append(f"   {item.summary[:200]}")
            lines.append(f"   🔗 {source} → {item.url}")
        lines.append("")
    return "\n".join(lines).strip()


def summarize(
    items: Sequence[Item],
    *,
    llm_cfg: Dict[str, Any],
    date_str: str,
    digest_title: str = "半导体新闻日报",
    window_hours: int = 24,
    max_items: int = 12,
    hints: Iterable[str] = (),
    extra_instructions: str = "",
    no_llm: bool = False,
    verbose: bool = True,
) -> str:
    """生成日报 markdown。no_llm=True 时走降级排版。"""
    if no_llm or not llm_cfg.get("api_key"):
        return fallback_markdown(
            items, date_str=date_str, digest_title=digest_title,
            window_hours=window_hours, max_items=max(max_items, 20),
        )

    messages = build_prompt(
        items,
        date_str=date_str,
        digest_title=digest_title,
        window_hours=window_hours,
        max_items=max_items,
        hints=hints,
        extra_instructions=extra_instructions,
    )
    if verbose:
        print(f"  [LLM] 候选 {len(items)} 条 → {llm_cfg.get('model')}")
    content = chat(
        messages,
        base_url=llm_cfg["base_url"],
        api_key=llm_cfg["api_key"],
        model=llm_cfg["model"],
        temperature=float(llm_cfg.get("temperature", 0.4)),
        max_tokens=int(llm_cfg.get("max_tokens", 4000)),
        timeout=int(llm_cfg.get("timeout", 180)),
        retries=int(llm_cfg.get("retries", 3)),
        verbose=verbose,
    )
    return clean_markdown(content)
