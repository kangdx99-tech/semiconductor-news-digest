"""提示词层：整个流水线的「核心资产」。

设计原则（都是从实际跑出来的教训里固化的）：
1. 模型只能从候选列表里挑，链接必须原样照抄 —— 模型编 URL 是这类工具最大的坑；
2. 链接内嵌在每条新闻之后，不允许末尾集中放链接；
3. 数字/型号/公司名原样保留，不做"约""左右"这类模糊化；
4. 宁缺毋滥：不够就少写，不硬凑条数，也不给"需手动确认"这类免责标签；
5. 搜索得到的选题提示只给标题，不给链接 —— 避免写出聚合页/跳转页链接。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .collector import Item

SYSTEM_PROMPT = """你是一位半导体行业的资深编辑，替一位每天都看芯片新闻的读者写日报。
你的判断标准是：这条消息会不会改变读者对行业的判断？只有会改变的才值得写。
你从不写空话、套话、预测性空谈；你只陈述事实，并且把数字、型号、公司名、时间原样保留。"""

SECTION_HINTS = """常见分组（按当天内容取舍，没有内容的组直接不写）：
【一句话总结】、【制造与代工】、【存储】、【AI芯片与算力】、【设备与材料】、【EDA与设计】、【终端与市场】、【政策与地缘】、【其他要点】"""

USER_TEMPLATE = """今天是 {date}，请写一份{digest_title}，覆盖{window_desc}的半导体行业要闻。读者用 10 分钟看完就要掌握今天的重点。

## 一、候选新闻（只能从这里面选，链接必须原样照抄）

{items_block}

## 二、选题提示（只有标题，没有链接，仅供你判断今天的热点方向）

{hints_block}

## 三、输出格式（严格遵守）

第一行就是标题：**{date} {digest_title}**

然后：

【一句话总结】
一句话说清今天最重要的事，以及它为什么重要。要具体，不要"行业持续发展"这种废话。

【分组名，例如 制造与代工】
① 标题（自己概括，不要照抄候选标题的原文句式）
来源：来源名 | 时间：YYYY-MM-DD
2-3 句话讲清事实：谁做了什么、关键数字、影响是什么。数字和型号原样保留，公司名/技术名保留英文原文。
🔗 来源名 → 链接（必须与候选列表里的 URL 完全一致，逐字符照抄）

② ...（同上）

## 四、硬性规则

1. 候选列表里没有的链接，一律不许出现在输出里；不许自己拼 URL、不许改 URL、不许省略 URL 的路径或参数。
2. 每条新闻的链接紧跟在该条内容之后，禁止在文末集中列链接。
3. 每个候选条目最多用一次；相同事件的不同报道合并成一条，选最权威的来源。
4. 只写与半导体产业链直接相关的内容：制程、存储、设备、材料、EDA、封装、
   算力芯片、芯片公司的资本动作与行业政策。
   **明确不要写的**：AI 应用/软件发布（聊天机器人、语音模型、App 更新）、
   终端产品发布与零售价格（手机、PC、键盘电脑、显卡零售价）、
   公司市值与股价日常波动、与芯片无关的反垄断/诉讼/监管新闻、操作系统与内核更新。
   判断标准：这条消息里**有没有晶圆、制程、产能、芯片设计、供应链**层面的信息。
   没有就不要写，哪怕它很热门。
5. 条数：素材充足时写 8-12 条；当天确实淡就少写。宁可 6 条扎实的，也不要凑 15 条注水的。最多 {max_items} 条。
6. 全中文输出，英文来源翻译成中文，专有名词保留英文。数字原样保留（不要写"约""左右"）。
7. 不要输出 markdown 代码块围栏（```），不要输出 "以下是" "好的" 这类开场白。
8. 不要写"需手动确认""来源待核实""可能被墙"这类标签。链接要么是候选列表里给的，要么就不要这条。"""


def _format_items(items: Sequence[Item], max_items: int = 40) -> str:
    lines: List[str] = []
    for idx, item in enumerate(items[:max_items], start=1):
        published = item.published.astimezone().strftime("%Y-%m-%d %H:%M") if item.published else "时间未知"
        lines.append(f"[{idx}] 来源：{item.source} | 时间：{published}")
        lines.append(f"    标题：{item.title}")
        if item.summary:
            lines.append(f"    摘要：{item.summary[:280]}")
        lines.append(f"    链接：{item.url}")
        lines.append("")
    return "\n".join(lines).strip() or "（今天没有抓到有效候选新闻）"


def _format_hints(hints: Iterable[str]) -> str:
    hints = [h for h in hints if h]
    if not hints:
        return "（无）"
    return "\n".join(f"- {h}" for h in hints)


def window_description(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"过去{days}天" if days > 1 else "过去24小时"
    return f"过去{hours}小时"


def build_prompt(
    items: Sequence[Item],
    *,
    date_str: str,
    digest_title: str = "半导体新闻日报",
    window_hours: int = 24,
    max_items: int = 12,
    hints: Iterable[str] = (),
    extra_instructions: str = "",
) -> List[Dict[str, str]]:
    """返回 OpenAI 兼容的 messages 列表。"""
    user = USER_TEMPLATE.format(
        date=date_str,
        digest_title=digest_title,
        window_desc=window_description(window_hours),
        items_block=_format_items(items),
        hints_block=_format_hints(hints),
        max_items=max_items,
    )
    if extra_instructions.strip():
        user += f"\n\n## 五、本期特别要求\n{extra_instructions.strip()}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
