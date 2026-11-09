"""数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RawItem:
    """从来源页面解析出的一条商品记录（尚未绑定来源名）。"""

    product_id: str
    title: str
    category: str
    price_cents: int
    currency: str
    in_stock: bool
    source_updated_at: str
