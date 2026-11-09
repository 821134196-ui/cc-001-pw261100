"""限流与重试：429（Retry-After）、503（退避）、断连重试、重试耗尽、频率与并发控制。"""
import time

from collector.engine import Engine
from collector.http_client import RateLimiter
from collector.mock_server import make_products


def test_retries_429_honoring_retry_after(db, mock):
    mock.state.flaky_429 = 2
    mock.state.retry_after = "0"  # 测试中不等真实秒数
    src = mock.json_source(url=f"{mock.base}/flaky/products", concurrency=1)
    result = Engine(db, [src]).run()["json-src"]
    assert result.status == "ok"
    assert db.count_products("json-src") == 25
    # 3 页 + 2 次 429 重试
    assert mock.state.request_counts["/flaky/products"] == 5


def test_retries_503_with_backoff(db, mock):
    mock.state.flaky_503 = 2
    src = mock.json_source(url=f"{mock.base}/flaky/products", concurrency=1)
    result = Engine(db, [src]).run()["json-src"]
    assert result.status == "ok"
    assert db.count_products("json-src") == 25
    assert mock.state.request_counts["/flaky/products"] == 5


def test_retries_dropped_connections(db, mock):
    mock.state.drops = 2  # 前 2 次请求连接被直接断开
    src = mock.json_source(url=f"{mock.base}/drop/products", concurrency=1)
    result = Engine(db, [src]).run()["json-src"]
    assert result.status == "ok"
    assert db.count_products("json-src") == 25
    assert mock.state.request_counts["/drop/products"] == 5


def test_gives_up_after_max_retries(db, mock):
    mock.state.flaky_429 = 10
    mock.state.retry_after = "0"
    src = mock.json_source(url=f"{mock.base}/flaky/products",
                           max_retries=2, concurrency=1)
    result = Engine(db, [src]).run()["json-src"]
    assert result.status == "failed"
    assert "429" in result.error
    assert mock.state.request_counts["/flaky/products"] == 3  # 1 次原始请求 + 2 次重试


def test_rate_limiter_enforces_min_interval():
    limiter = RateLimiter(0.05)
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - start >= 0.14  # 4 次获取之间至少 3 个间隔


def test_concurrency_setting_bounds_parallel_requests(db, mock):
    with mock.state.lock:
        mock.state.json_products = make_products("J", 50)  # 5 页
        mock.state.page_delay = 0.05
    src = mock.json_source(concurrency=3)
    result = Engine(db, [src]).run()["json-src"]
    assert result.status == "ok"
    assert db.count_products("json-src") == 50
    # 确实发生了并行抓取，且不超过配置的并发数
    assert 2 <= mock.state.max_inflight <= 3
