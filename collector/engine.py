"""采集引擎：每个来源独立线程运行（故障隔离），按页抓取并提交检查点。

- JSON 来源：先取第 1 页得知 total_pages，其余页按来源并发数并行抓取；
  检查点记录"已完成的连续页面前缀"，乱序完成的页不会导致漏采。
- HTML 来源：沿 next 链接顺序翻页，检查点记录下一个待抓 URL。
- 单页商品 upsert 与检查点推进在同一事务中（见 db.save_page），
  中断后从检查点续采，不漏采、不重复写入。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urljoin

from .config import SourceConfig
from .db import Database
from .http_client import HttpClient
from .parsers import parse_html_page, parse_json_page


class CollectionInterrupted(Exception):
    """采集中断（如达到 page_limit）；检查点保留，可续采。"""


@dataclass
class SourceResult:
    source: str
    status: str  # "ok" | "interrupted" | "failed"
    pages: int = 0
    items: int = 0
    error: str | None = None


class _Progress:
    def __init__(self):
        self.pages = 0
        self.items = 0


class Engine:
    def __init__(self, db: Database, sources: list[SourceConfig],
                 page_limit: int | None = None):
        self.db = db
        self.sources = {s.name: s for s in sources}
        self.page_limit = page_limit  # 每次运行最多抓取的页数（用于中断/限流演练）

    def run(self, only: set[str] | None = None) -> dict[str, SourceResult]:
        """并行采集所有（或指定）来源；单个来源失败不影响其他来源。"""
        results: dict[str, SourceResult] = {}
        lock = threading.Lock()

        def worker(src: SourceConfig):
            res = self._run_source(src)
            with lock:
                results[src.name] = res

        threads = []
        for src in self.sources.values():
            if only and src.name not in only:
                continue
            t = threading.Thread(target=worker, args=(src,),
                                 name=f"collect-{src.name}", daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return results

    # ------------------------------------------------------------------
    def _run_source(self, src: SourceConfig) -> SourceResult:
        run_id = self.db.start_run(src.name)
        progress = _Progress()
        status, error = "ok", None
        try:
            if src.type == "json":
                self._collect_json(src, progress)
            elif src.type == "html":
                self._collect_html(src, progress)
            else:
                raise ValueError(f"未知来源类型: {src.type!r}")
            self.db.clear_checkpoint(src.name)
        except CollectionInterrupted:
            status = "interrupted"
        except Exception as exc:  # 故障隔离：异常只影响本来源
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        self.db.finish_run(run_id, status, progress.pages, progress.items, error)
        return SourceResult(src.name, status, progress.pages, progress.items, error)

    def _check_limit(self, progress: _Progress) -> None:
        if self.page_limit is not None and progress.pages >= self.page_limit:
            raise CollectionInterrupted()

    def _client(self, src: SourceConfig) -> HttpClient:
        return HttpClient(src.min_interval, src.max_retries, src.backoff_base)

    # ------------------------------------------------------------------
    def _collect_json(self, src: SourceConfig, progress: _Progress) -> None:
        client = self._client(src)
        start = int(self.db.get_checkpoint(src.name) or "0") + 1
        done = set(range(1, start))  # 续采点之前的页视为已完成
        commit_lock = threading.Lock()

        def fetch(p: int) -> bytes:
            sep = "&" if "?" in src.url else "?"
            return client.get(f"{src.url}{sep}page={p}&size={src.page_size}")

        def commit(page_no: int, items) -> None:
            with commit_lock:
                self._check_limit(progress)
                done.add(page_no)
                cursor = 0
                while cursor + 1 in done:
                    cursor += 1
                self.db.save_page(src.name, items, str(cursor))
                progress.pages += 1
                progress.items += len(items)

        items, total_pages = parse_json_page(fetch(start), src.default_currency)
        commit(start, items)
        if total_pages > start:
            with ThreadPoolExecutor(max_workers=max(1, src.concurrency)) as pool:
                futures = [
                    pool.submit(
                        lambda p=p: commit(
                            p, parse_json_page(fetch(p), src.default_currency)[0]))
                    for p in range(start + 1, total_pages + 1)
                ]
                for f in futures:
                    f.result()  # 任一失败/中断则整个来源失败或中断

    def _collect_html(self, src: SourceConfig, progress: _Progress) -> None:
        client = self._client(src)
        url = self.db.get_checkpoint(src.name) or src.url
        while url:
            self._check_limit(progress)
            body = client.get(urljoin(src.url, url))
            items, next_href = parse_html_page(body, src.default_currency)
            # 检查点 = 下一个待抓 URL；没有下一页时清除
            self.db.save_page(src.name, items, next_href or "")
            progress.pages += 1
            progress.items += len(items)
            url = next_href
