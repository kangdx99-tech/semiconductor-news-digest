"""渲染与审计层：把模型输出收拾成可发布的内容，并做链接审计。

链接审计是这套流水线最值钱的一环：模型编 URL 是这类工具最难发现的坑（看起来
完全正常，点进去 404 或者跑到别的站）。这里的规则是「白名单制」——输出里出现的
每个 URL 都必须来自候选列表，否则整条新闻块直接丢掉，并在日志里记账。
"""

from __future__ import annotations

import re
import sys
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .collector import Item, canonical_url

URL_RE = re.compile(r"https?://[^\s\)\]\}>\"'\u4e00-\u9fff，。；、]+")
LINK_LINE_RE = re.compile(r"^\s*🔗")
TITLE_LINE_RE = re.compile(r"^\*\*.+\*\*\s*$")


def extract_urls(text: str) -> List[str]:
    return [m.group(0).rstrip(".,;、。，") for m in URL_RE.finditer(text or "")]


def _norm(url: str) -> str:
    return canonical_url(url).rstrip("/").lower()


def normalize_set(urls: Iterable[str]) -> Set[str]:
    return {_norm(u) for u in urls if u}


# ---------------------------------------------------------------- 链接审计
def audit_links(markdown: str, allowed_urls: Iterable[str]) -> Tuple[str, List[str]]:
    """丢掉含非法链接的新闻块。返回 (清洗后的 markdown, 被丢弃的 URL 列表)。

    以空行分块 —— 与提示词要求的输出格式天然对齐：每条新闻是一个独立块。
    """
    allowed = normalize_set(allowed_urls)
    blocks = re.split(r"\n\s*\n", markdown.strip())
    kept: List[str] = []
    removed: List[str] = []
    for block in blocks:
        urls = extract_urls(block)
        bad = [u for u in urls if _norm(u) not in allowed]
        if bad and len(bad) == len(set(urls)):
            # 整块链接全部不在白名单 → 判定为编造，整块丢弃
            removed.extend(bad)
            sys.stderr.write(f"  [审计] 丢弃编造链接的整块内容：{bad[0][:80]}\n")
            continue
        if bad:
            # 部分非法：只删掉非法的链接行，保留正文
            lines = [ln for ln in block.splitlines() if not (LINK_LINE_RE.match(ln) and extract_urls(ln) and _norm(extract_urls(ln)[0]) not in allowed)]
            removed.extend(bad)
            sys.stderr.write(f"  [审计] 删除非法链接行：{bad[0][:80]}\n")
            block = "\n".join(lines)
        kept.append(block)
    return "\n\n".join(kept).strip(), removed


# ---------------------------------------------------------------- 后处理
def ensure_title(markdown: str, date_str: str, digest_title: str) -> str:
    """保证第一行是加粗标题（飞书写入时会被识别成一级标题）。"""
    lines = markdown.strip().splitlines()
    expected = f"**{date_str} {digest_title}**"
    for idx, line in enumerate(lines):
        if line.strip():
            if TITLE_LINE_RE.match(line.strip()):
                return "\n".join(lines)
            lines.insert(idx, expected)
            lines.insert(idx + 1, "")
            return "\n".join(lines)
    return expected


def link_stats(markdown: str) -> Dict[str, int]:
    urls = extract_urls(markdown)
    return {"links": len(urls), "unique_links": len(set(urls)), "chars": len(markdown)}


def summarize_stats(markdown: str) -> str:
    stats = link_stats(markdown)
    return f"{stats['chars']} 字符 / {stats['unique_links']} 条链接"


def render(
    markdown: str,
    *,
    date_str: str,
    digest_title: str,
    allowed_items: Sequence[Item],
    verbose: bool = True,
) -> Tuple[str, List[str]]:
    """完整渲染管线：标题 → 链接审计。返回 (最终 markdown, 被丢弃的链接)。"""
    text = ensure_title(markdown, date_str, digest_title)
    cleaned, removed = audit_links(text, [item.url for item in allowed_items])
    if verbose:
        print(f"  [渲染] {summarize_stats(cleaned)}" + (f"，丢弃 {len(removed)} 条非法链接" if removed else ""))
    return cleaned, removed


# ---------------------------------------------------------------- 纯文本
def to_plain_text(markdown: str) -> str:
    """给飞书聊天/Telegram 推送用的纯文本版本。"""
    lines: List[str] = []
    for line in markdown.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"^#{1,6}\s*", "", line)
        if line.strip() == "---":
            lines.append("————————")
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def split_for_chat(text: str, limit: int = 3000) -> List[str]:
    """按段落切分长文本，适配即时通讯单条消息长度限制。"""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for block in text.split("\n\n"):
        if size + len(block) + 2 > limit and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks
