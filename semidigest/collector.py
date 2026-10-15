"""采集层：从 RSS / Atom 源抓取新闻条目，做时间窗过滤与去重。

只依赖 requests + 标准库，不引入 feedparser，降低别人的安装失败率。
解析策略：按标签 localname 遍历，天然兼容 RSS 2.0 / RDF / Atom 的各种命名空间写法。
"""

from __future__ import annotations

import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree as ET

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y/%m/%d %H:%M:%S",
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%d",
)


@dataclass
class Item:
    """一条候选新闻。"""

    title: str
    url: str
    source: str
    published: Optional[datetime] = None
    summary: str = ""
    status: Optional[int] = None  # 由 verifier 回填 HTTP 状态
    feed: str = ""
    keep: bool = True

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["published"] = self.published.isoformat() if self.published else None
        return data

    @property
    def published_str(self) -> str:
        if not self.published:
            return "未知时间"
        return self.published.astimezone().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 工具函数
def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_text(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(raw: Optional[str]) -> Optional[datetime]:
    """尽量把各种日期写法解析成 aware datetime（UTC）。"""
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    iso = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_title(title: str) -> str:
    """标题归一化，用于去重：去掉来源后缀、标点、大小写差异。"""
    text = title.strip()
    text = re.sub(r"\s+[-–—|]\s+[^-–—|]{1,40}$", "", text)  # 去掉 " - Reuters"
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())
    return text


def canonical_url(url: str) -> str:
    """去掉常见跟踪参数，便于按 URL 去重。"""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url.strip()
    query = "&".join(
        chunk
        for chunk in parts.query.split("&")
        if chunk and not chunk.lower().startswith(("utm_", "ref=", "fbclid", "gclid"))
    )
    return urlunparse((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", query, ""))


def strip_google_news_prefix(title: str) -> str:
    """Google News RSS 的标题尾部带 " - 来源名"，去掉以免污染摘要。"""
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()


# ---------------------------------------------------------------- 解析
def parse_feed(xml_bytes: bytes, source_name: str, feed_url: str = "") -> List[Item]:
    """解析 RSS 2.0 / RDF / Atom，返回 Item 列表。

    XML 不合法时做一次容错重试（真实世界里媒体源会间歇性吐出带控制字符的
    XML，直接放弃整条源太亏）。
    """
    def _parse(raw: bytes):
        root = ET.fromstring(raw)
        return [node for node in root.iter() if _localname(node.tag) in {"item", "entry"}]

    try:
        nodes = _parse(xml_bytes)
    except ET.ParseError as exc:
        repaired = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", xml_bytes)
        repaired = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", repaired)
        try:
            nodes = _parse(repaired)
            sys.stderr.write(f"[collector] XML 容错修复后解析成功 {source_name}（原错误：{exc}）\n")
        except ET.ParseError as exc2:
            sys.stderr.write(f"[collector] XML 解析失败 {source_name}: {exc2}\n")
            return []
    items: List[Item] = []
    for node in nodes:
        fields: Dict[str, str] = {}
        link_candidates: List[Tuple[str, str]] = []
        for child in node:
            name = _localname(child.tag)
            text = (child.text or "").strip()
            if name == "link":
                href = child.attrib.get("href", "")
                rel = child.attrib.get("rel", "alternate")
                link_candidates.append((rel, href or text))
            elif name in {"title", "description", "summary", "encoded", "content",
                          "pubdate", "published", "updated", "date", "dc:date"}:
                if name not in fields or len(text) > len(fields[name]):
                    fields[name] = text

        url = ""
        for rel, candidate in sorted(link_candidates, key=lambda x: 0 if x[0] in ("", "alternate") else 1):
            if candidate.startswith("http"):
                url = candidate
                break
        title = _clean_text(fields.get("title", ""))
        if not title or not url:
            continue

        published = None
        for key in ("pubdate", "published", "updated", "date", "dc:date"):
            published = parse_date(fields.get(key))
            if published:
                break

        summary = _clean_text(
            fields.get("description") or fields.get("summary") or fields.get("encoded") or ""
        )
        if len(summary) > 600:
            summary = summary[:600] + "…"

        items.append(
            Item(
                title=title,
                url=canonical_url(url),
                source=source_name,
                published=published,
                summary=summary,
                feed=feed_url,
            )
        )
    return items


def google_news_feed(query: str, hl: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    """把关键词转成 Google News RSS 搜索地址。"""
    from urllib.parse import quote_plus

    return (
        f"https://news.google.com/rss/search?q={quote_plus(query)}"
        f"&hl={hl}&gl={gl}&ceid={ceid}"
    )


# ---------------------------------------------------------------- 抓取
def fetch_feed(
    name: str,
    url: str,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> List[Item]:
    sess = session or requests
    try:
        resp = sess.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        sys.stderr.write(f"[collector] 抓取失败 {name} ({url}): {exc}\n")
        return []
    if resp.status_code != 200:
        sys.stderr.write(f"[collector] {name} 返回 HTTP {resp.status_code}，跳过\n")
        return []
    return parse_feed(resp.content, name, url)


def collect(
    sources: Iterable[Dict[str, Any]],
    searches: Iterable[Dict[str, Any]] = (),
    *,
    window_hours: int = 24,
    timeout: int = 20,
    max_per_source: int = 15,
    workers: int = 8,
    now: Optional[datetime] = None,
    keyword_filter: Sequence[str] = (),
    verbose: bool = True,
) -> List[Item]:
    """并发抓取所有源，过滤时间窗，去重，按时间倒序返回。

    keyword_filter: 关键词白名单。只对配置里标了 `filter: true` 的源生效 ——
    综合科技媒体（IT之家、cnBeta 之类）里半导体内容只占一小部分，
    不预过滤的话模型会被带偏，写出"苹果市值破 5 万亿"这种条目。
    垂直媒体的源不要标 filter，它们本来就全是相关内容。
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    keywords = [str(k).strip().lower() for k in (keyword_filter or []) if str(k).strip()]

    jobs: List[Tuple[str, str, bool]] = []
    for src in sources:
        src = src or {}
        url = src.get("url")
        if url:
            jobs.append((src.get("name") or urlparse(url).netloc, url, bool(src.get("filter"))))
    for search in searches:
        search = search or {}
        query = search.get("q") or search.get("query")
        if not query:
            continue
        url = google_news_feed(
            query,
            hl=search.get("hl", "en-US"),
            gl=search.get("gl", "US"),
            ceid=search.get("ceid", "US:en"),
        )
        jobs.append((search.get("name") or f"搜索:{query}", url, True))

    collected: List[Item] = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(fetch_feed, name, url, timeout, session): (name, url, do_filter)
                for name, url, do_filter in jobs
            }
            for future in as_completed(futures):
                name, url, do_filter = futures[future]
                try:
                    items = future.result()
                except Exception as exc:  # 单个源炸掉不影响整体
                    sys.stderr.write(f"[collector] {name} 异常: {exc}\n")
                    continue
                if do_filter and keywords:
                    before = len(items)
                    items = [
                        item
                        for item in items
                        if any(k in f"{item.title} {item.summary}".lower() for k in keywords)
                    ]
                    if verbose and before != len(items):
                        print(f"  [源] {name:28s} {len(items):3d} 条（关键词过滤掉 {before - len(items)} 条）")
                    elif verbose:
                        print(f"  [源] {name:28s} {len(items):3d} 条")
                elif verbose:
                    print(f"  [源] {name:28s} {len(items):3d} 条")
                collected.extend(items)

    # 时间窗 + 单源上限
    per_source: Dict[str, int] = {}
    fresh: List[Item] = []
    for item in collected:
        if item.published and item.published < cutoff:
            continue
        key = item.source
        per_source[key] = per_source.get(key, 0) + 1
        if per_source[key] > max_per_source:
            continue
        fresh.append(item)

    # 去重：标题归一化 + URL
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    unique: List[Item] = []
    for item in fresh:
        tkey = normalize_title(strip_google_news_prefix(item.title))
        ukey = canonical_url(item.url)
        if tkey in seen_titles or ukey in seen_urls:
            continue
        seen_titles.add(tkey)
        seen_urls.add(ukey)
        unique.append(item)

    unique.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    if verbose:
        print(f"  [采集] {len(collected)} 条 → 时间窗/去重后 {len(unique)} 条")
    return unique


def window_hours_for(moment: datetime, default: int = 24, monday: int = 72) -> int:
    """周一自动扩窗（周末几乎不发新闻，24 小时窗口会空）。"""
    return monday if moment.weekday() == 0 else default


# Google News / Yahoo News 这类搜索结果的链接是跳转壳，不能当原文链接写进日报。
AGGREGATOR_HOSTS = ("news.google.com", "news.yahoo.com", "flipboard.com")


def is_aggregator(item: Item) -> bool:
    """判断这条候选是不是「聚合/跳转页」链接。

    这类条目不会被写进日报正文，只把标题当作选题提示交给模型 —— 见 run.py。
    """
    url = (item.url or "").lower()
    return any(host in url for host in AGGREGATOR_HOSTS)

