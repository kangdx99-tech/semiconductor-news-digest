"""Markdown → 飞书 Docx block 转换（自带，不依赖任何 Hermes 环境）。

支持：
    **标题行**            → heading1
    ## 小节               → heading2
    ### 小节              → heading3
    ---                   → 分割线
    - 列表项              → 无序列表（保留 **加粗**）
    🔗 名称 → URL        → 文本块 + 超链接
    普通段落              → 文本块（保留行内 **加粗**）

飞书接口硬限制：单次最多 50 个 block，超过要分片。
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

API_BASE = "https://open.feishu.cn/open-apis"
MAX_BLOCKS_PER_CALL = 50


# ---------------------------------------------------------------- block 构造
def _run(content: str, bold: bool = False, link: Optional[str] = None) -> Dict[str, Any]:
    run: Dict[str, Any] = {"content": content}
    style: Dict[str, Any] = {}
    if bold:
        style["bold"] = True
    if link:
        style["link"] = {"url": link}
    if style:
        run["text_element_style"] = style
    return {"text_run": run}


def text_block(segments: Sequence[Tuple[str, bool, Optional[str]]]) -> Dict[str, Any]:
    return {
        "block_type": 2,
        "text": {
            "elements": [_run(text, bold, link) for text, bold, link in segments],
            "style": {"align": 1},
        },
    }


def bullet_block(segments: Sequence[Tuple[str, bool, Optional[str]]]) -> Dict[str, Any]:
    return {
        "block_type": 12,
        "bullet": {
            "elements": [_run(text, bold, link) for text, bold, link in segments],
            "style": {"align": 1},
        },
    }


def _heading(level: int, content: str) -> Dict[str, Any]:
    block_type = {1: 3, 2: 4, 3: 5, 4: 6}.get(level, 5)
    key = f"heading{level}"
    return {
        "block_type": block_type,
        key: {"elements": [_run(content, bold=True)], "style": {"align": 1}},
    }


def heading1_block(content: str) -> Dict[str, Any]:
    return _heading(1, content)


def heading2_block(content: str) -> Dict[str, Any]:
    return _heading(2, content)


def heading3_block(content: str) -> Dict[str, Any]:
    return _heading(3, content)


def divider_block() -> Dict[str, Any]:
    return {"block_type": 22, "divider": {}}


def parse_bold_segments(text: str) -> List[Tuple[str, bool]]:
    """把 `**加粗**` 拆成 (文本, 是否加粗) 片段。"""
    out: List[Tuple[str, bool]] = []
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append((part[2:-2], True))
        else:
            out.append((part, False))
    return out or [(text, False)]


def _link_segments(line: str) -> List[Tuple[str, bool, Optional[str]]]:
    """处理 `🔗 来源名 → https://...` 这一行，输出带超链接的片段。"""
    url_match = re.search(r"https?://\S+", line)
    label = line.replace("🔗", "").replace("→", " ").strip()
    if not url_match:
        return [(label, False, None)]
    url = url_match.group(0).rstrip(").,;")
    label = label.replace(url, "").strip().rstrip("→").strip() or url
    return [(f"{label} ", False, None), (url, False, url)]


def markdown_to_blocks(markdown: str) -> List[Dict[str, Any]]:
    """把日报 markdown 转成飞书 block 列表。"""
    blocks: List[Dict[str, Any]] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped == "---":
            blocks.append(divider_block())
            continue

        # 加粗标题行：**2026年10月15日 半导体新闻日报**
        if re.fullmatch(r"\*\*.+\*\*", stripped):
            blocks.append(heading1_block(stripped.strip("*").strip()))
            continue

        if stripped.startswith("### "):
            blocks.append(heading3_block(stripped[4:].replace("**", "").strip()))
            continue
        if stripped.startswith("## "):
            blocks.append(heading2_block(stripped[3:].replace("**", "").strip()))
            continue
        if stripped.startswith("# "):
            blocks.append(heading1_block(stripped[2:].replace("**", "").strip()))
            continue

        if stripped.startswith("🔗"):
            blocks.append(text_block(_link_segments(stripped)))
            continue

        if stripped.startswith(("- ", "* ")):
            segments = [(t, b, None) for t, b in parse_bold_segments(stripped[2:].strip())]
            blocks.append(bullet_block(segments))
            continue

        segments = [(t, b, None) for t, b in parse_bold_segments(stripped)]
        blocks.append(text_block(segments))
    return blocks


# ---------------------------------------------------------------- 接口调用
def tenant_access_token(app_id: str, app_secret: str, timeout: int = 20) -> str:
    resp = requests.post(
        f"{API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=timeout,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败：{data}")
    return data["tenant_access_token"]


def insert_blocks_at_top(
    token: str,
    doc_token: str,
    blocks: Sequence[Dict[str, Any]],
    *,
    batch_size: int = MAX_BLOCKS_PER_CALL,
    verbose: bool = True,
) -> int:
    """把 blocks 插到文档最开头（index=0），返回写入的 block 数。

    分片时 index 逐片递增，保证顺序与 markdown 一致；全程只 insert，不删除任何已有内容。
    """
    url = f"{API_BASE}/docx/v1/documents/{doc_token}/blocks/{doc_token}/children"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    written = 0
    for start in range(0, len(blocks), batch_size):
        batch = list(blocks[start : start + batch_size])
        payload = {"children": batch, "index": start}
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"写入第 {start // batch_size + 1} 片失败：HTTP {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"写入第 {start // batch_size + 1} 片失败：{data}")
        written += len(batch)
        if verbose:
            print(f"  [飞书文档] 第 {start // batch_size + 1} 片写入 {len(batch)} 个 block")
        time.sleep(0.3)
    return written


def read_raw_content(token: str, doc_token: str, timeout: int = 60) -> str:
    """读取文档全文纯文本（用于写完后的自检）。"""
    resp = requests.get(
        f"{API_BASE}/docx/v1/documents/{doc_token}/raw_content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"读取文档失败：{data}")
    return data["data"]["content"]


def send_chat_text(
    token: str,
    chat_id: str,
    text: str,
    *,
    max_len: int = 3000,
    verbose: bool = True,
) -> List[str]:
    """往飞书群/单聊发纯文本消息，返回 message_id 列表。"""
    from .renderer import split_for_chat

    ids: List[str] = []
    for chunk in split_for_chat(text, limit=max_len):
        resp = requests.post(
            f"{API_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": chunk}, ensure_ascii=False)},
            timeout=60,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"发送飞书消息失败：{data}")
        ids.append(data["data"]["message_id"])
        if verbose:
            print(f"  [飞书聊天] 已发送 message_id={ids[-1]}")
    return ids


if __name__ == "__main__":  # pragma: no cover
    text = sys.stdin.read()
    print(json.dumps(markdown_to_blocks(text), ensure_ascii=False, indent=2))
