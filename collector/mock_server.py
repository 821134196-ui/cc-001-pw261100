"""本地模拟数据源：分页 JSON 接口 + HTML 商品列表，不访问任何真实网站。

可控故障模式（供测试与演示）：
- /flaky/products  前 N 次返回 429（带 Retry-After）或 503，之后正常
- /drop/products   前 N 次直接断开连接，之后正常
- /broken-json/products、/broken-html/products  返回无法解析的内容
"""
from __future__ import annotations

import html
import json
import math
import socket
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

CATEGORIES = ["Tools", "Home", "Garden", "Office"]
HTML_PAGE_SIZE = 8


def make_products(prefix: str, count: int) -> list[dict]:
    """生成确定性的模拟商品数据。"""
    out = []
    for i in range(1, count + 1):
        out.append({
            "id": f"{prefix}{i:03d}",
            "title": f"Product {prefix}{i:03d}",
            "category": CATEGORIES[i % len(CATEGORIES)],
            "price": f"{9.99 + i:.2f}",
            "currency": "USD",
            "in_stock": i % 5 != 0,
            "updated_at": f"2026-11-{(i % 28) + 1:02d}T08:00:00+00:00",
        })
    return out


class MockState:
    """mock 服务器的可变状态（测试/CLI 可实时修改）。"""

    def __init__(self, json_count: int = 25, html_count: int = 20):
        self.lock = threading.Lock()
        self.json_products = make_products("J", json_count)
        self.html_products = make_products("H", html_count)
        self.flaky_429 = 0        # /flaky/* 剩余返回 429 的次数
        self.flaky_503 = 0        # /flaky/* 剩余返回 503 的次数
        self.drops = 0            # /drop/* 剩余断连次数
        self.retry_after = "1"    # 429 响应携带的 Retry-After 头
        self.page_delay = 0.0     # 每个请求的人为延迟（秒），用于并发测试
        self.request_counts: Counter = Counter()
        self.inflight = 0         # 当前在处理的请求数
        self.max_inflight = 0     # 历史最大并发请求数


def render_html_page(products: list[dict], page: int, total_pages: int) -> str:
    rows = []
    for p in products:
        stock = "in_stock" if p["in_stock"] else "out_of_stock"
        rows.append(
            f'<div class="product" data-id="{html.escape(p["id"])}"'
            f' data-updated="{html.escape(p["updated_at"])}">'
            f'<span class="title">{html.escape(p["title"])}</span>'
            f'<span class="category">{html.escape(p["category"])}</span>'
            f'<span class="price">${html.escape(p["price"])}</span>'
            f'<span class="stock">{stock}</span>'
            f'</div>'
        )
    nxt = (f'<a class="next" href="/html/products?page={page + 1}">Next</a>'
           if page < total_pages else "")
    return ("<html><body>"
            f'<div class="products" data-total-pages="{total_pages}">'
            + "".join(rows) + "</div>" + nxt + "</body></html>")


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # 客户端断连不刷屏
        pass


def create_mock_server(state: MockState | None = None, host: str = "127.0.0.1",
                       port: int = 0):
    """创建 mock 服务器（未启动），返回 (server, state)。"""
    state = state or MockState()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # 静默访问日志
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            with state.lock:
                state.request_counts[path] += 1
                state.inflight += 1
                state.max_inflight = max(state.max_inflight, state.inflight)
            try:
                if state.page_delay > 0:
                    time.sleep(state.page_delay)
                self._dispatch(path)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with state.lock:
                    state.inflight -= 1

        # ---------------- 响应辅助 ----------------
        def _send(self, code, body=b"", ctype="text/plain", headers=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json_page(self, get_products):
            q = parse_qs(urlsplit(self.path).query)
            try:
                page = int(q.get("page", ["1"])[0])
                size = int(q.get("size", ["10"])[0])
            except ValueError:
                return self._send(400, "bad page/size")
            size = max(1, size)
            with state.lock:
                items = list(get_products())
            total = len(items)
            total_pages = max(1, math.ceil(total / size))
            lo = (page - 1) * size
            chunk = items[lo:lo + size] if 0 < page <= total_pages else []
            payload = {"items": chunk, "page": page, "size": size,
                       "total": total, "total_pages": total_pages}
            self._send(200, json.dumps(payload), "application/json")

        def _html_page(self):
            q = parse_qs(urlsplit(self.path).query)
            try:
                page = int(q.get("page", ["1"])[0])
            except ValueError:
                return self._send(400, "bad page")
            with state.lock:
                items = list(state.html_products)
            total_pages = max(1, math.ceil(len(items) / HTML_PAGE_SIZE))
            lo = (page - 1) * HTML_PAGE_SIZE
            chunk = items[lo:lo + HTML_PAGE_SIZE] if 0 < page <= total_pages else []
            self._send(200, render_html_page(chunk, page, total_pages), "text/html")

        # ---------------- 路由 ----------------
        def _dispatch(self, path):
            if path == "/json/products":
                self._json_page(lambda: state.json_products)
            elif path == "/html/products":
                self._html_page()
            elif path == "/flaky/products":
                with state.lock:
                    if state.flaky_429 > 0:
                        state.flaky_429 -= 1
                        return self._send(429, "slow down",
                                          headers={"Retry-After": state.retry_after})
                    if state.flaky_503 > 0:
                        state.flaky_503 -= 1
                        return self._send(503, "unavailable")
                self._json_page(lambda: state.json_products)
            elif path == "/drop/products":
                with state.lock:
                    drop = state.drops > 0
                    if drop:
                        state.drops -= 1
                if drop:  # 模拟网络层故障：直接断开连接
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return
                self._json_page(lambda: state.json_products)
            elif path == "/broken-json/products":
                self._send(200, '{"items": [{"id": "X1", "title": ',
                           "application/json")
            elif path == "/broken-html/products":
                self._send(200, '<html><body><div class="products">'
                           '<div class="product"><span class="title">No ID</span></div>'
                           '</div></body></html>', "text/html")
            else:
                self._send(404, "not found")

    server = _QuietThreadingHTTPServer((host, port), Handler)
    return server, state
