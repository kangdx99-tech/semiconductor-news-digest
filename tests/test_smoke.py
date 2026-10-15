"""离线自检：不联网、不调用模型，验证核心逻辑。

    python -m pytest tests/ -q
    python tests/test_smoke.py        # 不装 pytest 也能跑
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from semidigest.collector import (  # noqa: E402
    Item,
    canonical_url,
    google_news_feed,
    is_aggregator,
    normalize_title,
    parse_date,
    parse_feed,
    window_hours_for,
)
from semidigest.config import Config, _deep_merge, load_dotenv  # noqa: E402
from semidigest.feishu_blocks import markdown_to_blocks, parse_bold_segments  # noqa: E402
from semidigest.renderer import (  # noqa: E402
    audit_links,
    ensure_title,
    extract_urls,
    split_for_chat,
    to_plain_text,
)
from semidigest.summarizer import clean_markdown, fallback_markdown  # noqa: E402

RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>TSMC 2nm ramp &amp; yields</title>
    <link>https://example.com/a?utm_source=newsletter&amp;id=1</link>
    <pubDate>Tue, 14 Oct 2026 10:00:00 +0000</pubDate>
    <description>&lt;p&gt;TSMC said &lt;b&gt;2nm&lt;/b&gt; yields are on track.&lt;/p&gt;</description>
  </item>
  <item>
    <title>TSMC 2nm ramp &amp; yields</title>
    <link>https://mirror.example.org/dup</link>
    <pubDate>Tue, 14 Oct 2026 09:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Intel foundry update - Reuters</title>
    <link>https://example.com/intel</link>
    <pubDate>Mon, 13 Oct 2026 08:00:00 +0000</pubDate>
  </item>
</channel></rss>"""

ATOM_SAMPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test</title>
  <entry>
    <title>Samsung HBM4 qualification</title>
    <link rel="alternate" href="https://example.org/samsung-hbm4"/>
    <updated>2026-10-14T12:30:00Z</updated>
    <summary>Samsung passed HBM4 qualification.</summary>
  </entry>
</feed>"""


# ---------------------------------------------------------------- 采集
def test_parse_rss():
    items = parse_feed(RSS_SAMPLE, "Test")
    assert len(items) == 3
    assert items[0].title == "TSMC 2nm ramp & yields"
    assert "&lt;" not in items[0].summary and "<b>" not in items[0].summary
    assert "2nm" in items[0].summary


def test_parse_atom():
    items = parse_feed(ATOM_SAMPLE, "AtomTest")
    assert len(items) == 1
    assert items[0].url == "https://example.org/samsung-hbm4"
    assert items[0].published is not None
    assert items[0].published.year == 2026


def test_canonical_url_strips_tracking():
    assert "utm_source" not in canonical_url("https://example.com/a?utm_source=x&id=1")
    assert canonical_url("https://example.com/a/") == "https://example.com/a"


def test_normalize_title_collapses_punctuation():
    assert normalize_title("TSMC 2nm ramp & yields") == normalize_title("TSMC  2nm ramp  yields")
    assert normalize_title("Intel foundry update - Reuters") == normalize_title("Intel foundry update")


def test_parse_date_formats():
    assert parse_date("Tue, 14 Oct 2026 10:00:00 +0000").year == 2026
    assert parse_date("2026-10-14T12:30:00Z").hour == 12
    assert parse_date("garbage") is None
    assert parse_date(None) is None


def test_monday_window():
    monday = datetime(2026, 10, 12, 9, 0)  # 2026-10-12 是周一
    tuesday = datetime(2026, 10, 13, 9, 0)
    assert window_hours_for(monday, 24, 72) == 72
    assert window_hours_for(tuesday, 24, 72) == 24


def test_aggregator_detection():
    assert is_aggregator(Item(title="x", url="https://news.google.com/rss/articles/abc", source="s"))
    assert not is_aggregator(Item(title="x", url="https://semiengineering.com/a", source="s"))


def test_google_news_feed_url():
    url = google_news_feed("semiconductor chip news")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "hl=en-US" in url


# ---------------------------------------------------------------- 渲染 / 审计
def test_audit_drops_hallucinated_block():
    md = (
        "【A】\n① 合法条目\n🔗 X → https://example.com/a\n\n"
        "【B】\n② 编造条目\n🔗 Y → https://totally-fake-domain.example/zzz"
    )
    cleaned, removed = audit_links(md, ["https://example.com/a"])
    assert "totally-fake-domain" not in cleaned
    assert "合法条目" in cleaned
    assert len(removed) == 1


def test_audit_keeps_trailing_slash_variant():
    md = "【A】\n① 条目\n🔗 X → https://example.com/a/"
    cleaned, removed = audit_links(md, ["https://example.com/a"])
    assert not removed
    assert "example.com/a" in cleaned


def test_ensure_title_inserts_header():
    out = ensure_title("【一句话总结】\n内容", "2026-10-15", "半导体新闻日报")
    assert out.startswith("**2026-10-15 半导体新闻日报**")
    # 已有标题时不重复插入
    again = ensure_title(out, "2026-10-15", "半导体新闻日报")
    assert again.count("半导体新闻日报**") == 1


def test_extract_urls_ignores_chinese_punctuation():
    urls = extract_urls("看这里 https://example.com/a，还有 https://example.com/b。")
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_to_plain_text_strips_markers():
    text = to_plain_text("**标题**\n- 项目 **加粗**")
    assert "**" not in text
    assert "标题" in text


def test_split_for_chat_respects_limit():
    chunks = split_for_chat("段落\n\n" * 500, limit=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_clean_markdown_unwraps_fence():
    assert clean_markdown("```markdown\n内容\n```") == "内容"
    assert clean_markdown("好的，以下是日报：\n**标题**") == "**标题**"


# ---------------------------------------------------------------- 飞书 block
def test_markdown_to_blocks_shapes():
    md = (
        "**2026年10月15日 半导体新闻日报**\n\n"
        "【一句话总结】\n测试。\n\n"
        "【制造与代工】\n"
        "① 标题\n"
        "来源：Test | 时间：2026-10-15\n"
        "正文内容，带 **加粗**。\n"
        "🔗 Test → https://example.com/a\n"
    )
    blocks = markdown_to_blocks(md)
    assert blocks[0]["block_type"] == 3          # 一级标题
    assert any(b["block_type"] == 2 for b in blocks)  # 正文
    link_blocks = [
        b for b in blocks
        if b["block_type"] == 2
        and any("link" in el["text_run"].get("text_element_style", {}) for el in b["text"]["elements"])
    ]
    assert link_blocks, "链接行应生成带超链接的文本块"
    assert len(blocks) <= 50


def test_parse_bold_segments():
    segs = parse_bold_segments("**A** 和 **B** 和 C")
    assert ("A", True) in segs and ("B", True) in segs
    assert any(text.strip() == "和 C" and not bold for text, bold in segs)


# ---------------------------------------------------------------- 配置
def test_deep_merge():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    merged = _deep_merge(base, {"a": {"y": 9}})
    assert merged["a"] == {"x": 1, "y": 9} and merged["b"] == 3


def test_config_dotted_get(tmp_path):
    (tmp_path / "c.yaml").write_text(
        "llm:\n  model: test-model\ndigest:\n  max_items: 7\n", encoding="utf-8"
    )
    cfg = Config.load(str(tmp_path / "c.yaml"), base_dir=tmp_path)
    assert cfg.get("llm.model") == "test-model"
    assert cfg.get("digest.max_items") == 7
    assert cfg.get("nope.missing", "fallback") == "fallback"
    assert isinstance(cfg.sources(), list)
    assert cfg.deliveries()


def test_env_loader(tmp_path):
    env = tmp_path / ".env"
    env.write_text('FOO_BAR_TEST=abc\n# comment\nexport BAZ_Q="quoted"\n', encoding="utf-8")
    load_dotenv(env, override=True)
    import os

    assert os.environ["FOO_BAR_TEST"] == "abc"
    assert os.environ["BAZ_Q"] == "quoted"


# ---------------------------------------------------------------- 降级
def test_fallback_markdown_without_llm():
    items = [
        Item(title="A", url="https://a.com/x", source="SrcA", published=datetime.now(timezone.utc)),
        Item(title="B", url="https://b.com/y", source="SrcB", published=datetime.now(timezone.utc)),
    ]
    md = fallback_markdown(items, date_str="2026-10-15")
    assert md.startswith("**2026-10-15 半导体新闻日报**")
    assert "https://a.com/x" in md and "https://b.com/y" in md


def _run_standalone() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for name, fn in tests:
        try:
            if fn.__code__.co_argcount:
                import tempfile

                with tempfile.TemporaryDirectory() as tmp:
                    fn(Path(tmp))
            else:
                fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"✅ {name}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
