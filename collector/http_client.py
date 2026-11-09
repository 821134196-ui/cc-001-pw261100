"""HTTP 抓取：按来源限流（最小请求间隔），429/503/临时网络错误自动重试。

重试策略：优先服从响应头 Retry-After，否则指数退避（base * 2^attempt + 抖动）。
"""
from __future__ import annotations

import http.client
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RETRYABLE_STATUS = (429, 503)
# 临时网络错误：连接重置/拒绝/超时、对端断开、DNS 失败等
TRANSIENT_ERRORS = (
    urllib.error.URLError,
    ConnectionError,
    TimeoutError,
    http.client.HTTPException,
    OSError,
)


class FetchError(Exception):
    """请求最终失败（不可重试的状态码，或重试次数耗尽）。"""


class RateLimiter:
    """保证两次获取之间的最小间隔（线程安全）。"""

    def __init__(self, min_interval: float):
        self.min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next_at)
            self._next_at = at + self.min_interval
        delay = at - now
        if delay > 0:
            time.sleep(delay)


class HttpClient:
    def __init__(self, min_interval: float = 0.0, max_retries: int = 5,
                 backoff_base: float = 0.5, timeout: float = 10.0):
        self._limiter = RateLimiter(min_interval)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout

    def get(self, url: str) -> bytes:
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "local-product-collector/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code not in RETRYABLE_STATUS or attempt >= self.max_retries:
                    raise FetchError(f"GET {url} 失败: HTTP {e.code}") from e
                delay = _retry_after_seconds(e.headers.get("Retry-After"))
                if delay is None:
                    delay = self._backoff(attempt)
            except TRANSIENT_ERRORS as e:
                if attempt >= self.max_retries:
                    raise FetchError(
                        f"GET {url} 失败: {type(e).__name__}: {e}") from e
                delay = self._backoff(attempt)
            if delay > 0:
                time.sleep(delay)
        raise FetchError(f"GET {url} 失败")  # pragma: no cover - 不可达

    def _backoff(self, attempt: int) -> float:
        return self.backoff_base * (2 ** attempt) + random.uniform(0, self.backoff_base)


def _retry_after_seconds(value: str | None) -> float | None:
    """解析 Retry-After 头（秒数或 HTTP 日期）。"""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None
