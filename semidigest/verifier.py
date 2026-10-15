"""链接校验层：并发检查每条新闻的原始链接是否真的可访问。

规则（沿用这套流水线一直在用的硬规矩）：
- 只保留 HTTP 200-399 的链接；
- 403 视为「可达但反爬」——很多正经媒体对 HEAD/脚本请求返回 403，
  默认按可接受处理（accept_403: true），可通过配置关掉；
- 校验失败 > 通过时不静默放行，而是标记出来交给上层删除条目，绝不写「需手动确认」。
"""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

import requests

from .collector import USER_AGENT, Item


@dataclass
class VerifyResult:
    url: str
    status: Optional[int]
    ok: bool
    error: str = ""
    method: str = ""

    @property
    def label(self) -> str:
        host = ""
        try:
            host = urlparse(self.url).netloc.replace("www.", "")
        except ValueError:
            pass
        if self.error:
            short = re.sub(r"HTTPSConnectionPool\(host='[^']*'[^)]*\)", "网络错误", self.error)
            return f"{host or '?'} {short[:26]}"
        return f"{host} HTTP {self.status}"


def check_url(
    url: str,
    *,
    timeout: int = 15,
    accept_403: bool = True,
    session: Optional[requests.Session] = None,
    retries: int = 1,
) -> VerifyResult:
    """HEAD 优先，失败或 4xx/5xx 时用 GET 重试（很多站点不支持 HEAD）。

    retries: 网络类错误（超时/DNS）的重试次数 —— 这类失败经常是瞬时的，
              重试一次能救回不少条目；HTTP 状态码类失败不重试，重试也没用。
    """
    sess = session or requests
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }

    last_error = ""
    for attempt in range(max(1, retries + 1)):
        for method in ("HEAD", "GET"):
            try:
                resp = sess.request(
                    method, url, timeout=timeout, headers=headers,
                    allow_redirects=True, stream=(method == "GET"),
                )
                status = resp.status_code
                if method == "GET":
                    resp.close()
                if 200 <= status < 400:
                    return VerifyResult(url, status, True, method=method)
                if status == 403 and accept_403:
                    return VerifyResult(url, status, True, method=method, error="")
                if method == "HEAD" and status in (400, 403, 405, 406, 501):
                    continue  # 换 GET 再试
                return VerifyResult(url, status, False, method=method)
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
    return VerifyResult(url, None, False, error=last_error or "request failed")


def verify_items(
    items: Sequence[Item],
    *,
    timeout: int = 15,
    workers: int = 12,
    accept_403: bool = True,
    retries: int = 1,
    verbose: bool = True,
) -> List[Item]:
    """就地回填 item.status / item.keep，返回原列表。"""
    if not items:
        return list(items)

    results: Dict[str, VerifyResult] = {}
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(
                    check_url, item.url, timeout=timeout, accept_403=accept_403,
                    session=session, retries=retries,
                ): item.url
                for item in items
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results[url] = future.result()
                except Exception as exc:
                    results[url] = VerifyResult(url, None, False, error=str(exc))

    dropped = 0
    for item in items:
        result = results.get(item.url)
        if result is None:
            continue
        item.status = result.status
        item.keep = result.ok
        if not result.ok:
            dropped += 1
            if verbose:
                print(f"  [丢弃] {result.label:<28s} {item.title[:52]}")
    if verbose:
        print(f"  [校验] {len(items) - dropped}/{len(items)} 条链接通过")
    return list(items)


def verified_items(items: Iterable[Item]) -> List[Item]:
    return [item for item in items if getattr(item, "keep", True)]


def verify_urls(urls: Iterable[str], **kwargs) -> Dict[str, VerifyResult]:
    """给单独验链接用（CLI: python -m semidigest.verifier URL...）。"""
    urls = [u for u in urls if u]
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("accept_403", True)
    kwargs.setdefault("retries", 1)
    workers = int(kwargs.pop("workers", 12))
    out: Dict[str, VerifyResult] = {}
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(check_url, u, session=session, **kwargs): u for u in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    out[url] = future.result()
                except Exception as exc:
                    out[url] = VerifyResult(url, None, False, error=str(exc))
    return out


def _main(argv: List[str]) -> int:
    if not argv:
        print("用法: python -m semidigest.verifier <URL> [URL ...]")
        return 2
    results = verify_urls(argv)
    bad = 0
    for url, result in results.items():
        mark = "✅" if result.ok else "❌"
        print(f"{mark} {result.label:>14s}  {url}")
        bad += 0 if result.ok else 1
    print(f"\n通过 {len(results) - bad}/{len(results)}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
