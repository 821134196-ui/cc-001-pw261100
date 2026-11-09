"""页面解析：JSON 分页接口响应 和 HTML 商品列表页。"""
from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser

from .models import RawItem


class ParseError(Exception):
    """页面无法解析为商品列表时抛出。"""


_CURRENCY_SYMBOLS = {
    "US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY", "￥": "CNY",
}
_PRICE_RE = re.compile(r"^([0-9][0-9,]*(?:\.[0-9]+)?)(?:\s*([A-Za-z]{3}))?$")


def parse_price(text: str, currency: str = "") -> tuple[int, str]:
    """把 "12.34" / "$12.34" / "12.34 USD" 解析为 (分, 币种)。"""
    text = (text or "").strip()
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if text.startswith(sym):
            currency = _CURRENCY_SYMBOLS[sym]
            text = text[len(sym):].strip()
            break
    m = _PRICE_RE.match(text)
    if not m:
        raise ParseError(f"无法解析价格: {text!r}")
    if m.group(2):
        currency = m.group(2)
    cents = int((Decimal(m.group(1).replace(",", "")) * 100).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))
    return cents, (currency or "").upper()


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "in_stock", "in stock", "有货"):
        return True
    if s in ("false", "0", "no", "out_of_stock", "out of stock", "缺货"):
        return False
    raise ParseError(f"无法解析库存状态: {value!r}")


# ---------------------------------------------------------------------------
# JSON 接口
# ---------------------------------------------------------------------------

def parse_json_page(body: bytes, default_currency: str = "") -> tuple[list[RawItem], int]:
    """解析 {"items": [...], "total_pages": N}，返回 (商品列表, 总页数)。"""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise ParseError(f"非法 JSON 响应: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ParseError("JSON 响应必须是包含 'items' 数组的对象")
    total_pages = data.get("total_pages")
    if not isinstance(total_pages, int) or isinstance(total_pages, bool) or total_pages < 1:
        raise ParseError("JSON 响应缺少合法的 'total_pages'")
    return [_json_item(r, default_currency) for r in data["items"]], total_pages


def _json_item(raw, default_currency: str) -> RawItem:
    if not isinstance(raw, dict):
        raise ParseError(f"JSON 商品条目必须是对象: {raw!r}")
    pid = "" if raw.get("id") is None else str(raw["id"]).strip()
    title = "" if raw.get("title") is None else str(raw["title"]).strip()
    if not pid or not title:
        raise ParseError(f"JSON 商品缺少 'id' 或 'title': {raw!r}")
    cents, currency = parse_price(
        str(raw.get("price", "")), str(raw.get("currency") or default_currency))
    if not currency:
        raise ParseError(f"JSON 商品 {pid!r} 缺少币种")
    return RawItem(
        product_id=pid,
        title=title,
        category=str(raw.get("category") or ""),
        price_cents=cents,
        currency=currency,
        in_stock=_as_bool(raw.get("in_stock", False)),
        source_updated_at=str(raw.get("updated_at") or ""),
    )


# ---------------------------------------------------------------------------
# HTML 列表页
# ---------------------------------------------------------------------------

class _ProductHTMLParser(HTMLParser):
    """解析如下结构的页面：

    <div class="product" data-id="P001" data-updated="2026-11-01T08:00:00+00:00">
      <span class="title">...</span><span class="category">...</span>
      <span class="price">$19.99</span><span class="stock">in_stock</span>
    </div>
    <a class="next" href="/products?page=2">Next</a>
    """

    ITEM_FIELDS = ("title", "category", "price", "stock")

    def __init__(self, default_currency: str = ""):
        super().__init__(convert_charrefs=True)
        self.default_currency = default_currency
        self.items: list[RawItem] = []
        self.next_href: str | None = None
        self._cur: dict | None = None
        self._depth = 0
        self._field: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if self._cur is not None:
            if tag == "div":
                self._depth += 1
            elif tag == "span":
                for f in self.ITEM_FIELDS:
                    if f in classes:
                        self._field, self._buf = f, []
                        break
        elif tag == "div" and "product" in classes:
            self._cur = {
                "product_id": attrs.get("data-id") or "",
                "source_updated_at": attrs.get("data-updated") or "",
            }
            self._depth = 0
        if tag == "a" and "next" in classes and attrs.get("href"):
            self.next_href = attrs["href"]

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self._field:
            self._cur[self._field] = "".join(self._buf).strip()
            self._field = None
        elif tag == "div" and self._cur is not None:
            if self._depth > 0:
                self._depth -= 1
            else:
                self._finalize()

    def _finalize(self):
        cur, self._cur = self._cur, None
        pid = cur["product_id"].strip()
        title = (cur.get("title") or "").strip()
        if not pid or not title:
            raise ParseError(f"HTML 商品块缺少 data-id 或标题: {cur!r}")
        cents, currency = parse_price(cur.get("price", ""), self.default_currency)
        if not currency:
            raise ParseError(f"HTML 商品 {pid!r} 缺少币种")
        self.items.append(RawItem(
            product_id=pid,
            title=title,
            category=(cur.get("category") or "").strip(),
            price_cents=cents,
            currency=currency,
            in_stock=_as_bool(cur.get("stock") or "false"),
            source_updated_at=cur["source_updated_at"].strip(),
        ))


def parse_html_page(body: bytes | str, default_currency: str = "") -> tuple[list[RawItem], str | None]:
    """解析 HTML 商品列表页，返回 (商品列表, 下一页链接或 None)。"""
    parser = _ProductHTMLParser(default_currency)
    parser.feed(body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body)
    parser.close()
    return parser.items, parser.next_href
