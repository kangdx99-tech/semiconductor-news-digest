#!/usr/bin/env python3
"""把日报 markdown 渲染成一张 PNG 截图，用于 README / 抖音图文素材。

    python3 scripts/make_screenshot.py output/2026-10-15.md docs/screenshot.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 1080
PAD = 56
BG = (255, 255, 255)
BORDER = (226, 232, 240)
TITLE = (15, 23, 42)
HEAD = (37, 99, 235)
BODY = (51, 65, 85)
LINK = (37, 99, 235)
MUTED = (100, 116, 139)

FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=index)
            except Exception:  # noqa: BLE001
                continue
    raise SystemExit("找不到可用的中文字体")


def render(markdown: str, out_path: str, limit_lines: int = 26) -> None:
    title_font = load_font(34)
    head_font = load_font(27)
    body_font = load_font(23)
    small_font = load_font(20)
    mono_font = load_font(19)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=18, outline=BORDER, width=2)

    y = PAD
    x = PAD
    max_width = W - PAD * 2
    line_no = 0

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            y += 12
            continue
        if line_no >= limit_lines:
            break

        # 标题行 **...**
        if line.startswith("**") and line.rstrip().endswith("**"):
            text = line.strip("*").strip()
            draw.text((x, y), text, font=title_font, fill=TITLE)
            y += 54
            line_no += 1
            continue

        # 分组标题 【...】
        if line.startswith("【") and "】" in line and len(line) < 40:
            draw.text((x, y), line, font=head_font, fill=HEAD)
            y += 44
            line_no += 1
            continue

        # 链接行
        if line.startswith("🔗"):
            prefix = line.split("→")[0].replace("🔗", "").strip()
            url = line.split("→")[-1].strip() if "→" in line else ""
            draw.text((x, y), f"链接 → {prefix}", font=mono_font, fill=MUTED)
            y += 30
            draw.text((x, y), url[:96], font=mono_font, fill=LINK)
            y += 38
            line_no += 1
            continue

        # 序号行 / 普通正文：按宽度折行
        font = body_font if not line.startswith(("来源：", "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫")) else small_font
        fill = TITLE if line.startswith(("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫")) else BODY
        text = line.replace("**", "")
        current = ""
        for ch in text:
            if draw.textlength(current + ch, font=font) > max_width:
                draw.text((x, y), current, font=font, fill=fill)
                y += int(font.size * 1.55)
                current = ch
            else:
                current += ch
        if current:
            draw.text((x, y), current, font=font, fill=fill)
            y += int(font.size * 1.65)
        line_no += 1
        if y > H - PAD - 60:
            break

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"已生成 {out_path}  ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    src = Path(sys.argv[1])
    dst = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshot.png"
    render(src.read_text(encoding="utf-8"), dst)
