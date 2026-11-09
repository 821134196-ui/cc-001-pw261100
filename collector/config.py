"""来源配置：每个来源可独立设置并发、请求频率和重试参数。"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class SourceConfig:
    name: str
    type: str  # "json" | "html"
    url: str
    page_size: int = 10            # 仅 JSON 来源：每页条数
    concurrency: int = 2           # 该来源的最大并发请求数
    min_interval: float = 0.0      # 该来源两次请求的最小间隔（秒）
    max_retries: int = 5           # 429/503/网络错误的最大重试次数
    backoff_base: float = 0.5      # 指数退避基数（秒）
    default_currency: str = "USD"  # 页面未标明币种时的默认币种


def load_sources(path: str) -> list[SourceConfig]:
    """从 JSON 配置文件加载来源列表。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: 需要一个非空的 'sources' 数组")
    configs: list[SourceConfig] = []
    seen: set[str] = set()
    for i, r in enumerate(raw):
        try:
            cfg = SourceConfig(
                name=str(r["name"]),
                type=str(r["type"]),
                url=str(r["url"]),
                page_size=int(r.get("page_size", 10)),
                concurrency=int(r.get("concurrency", 2)),
                min_interval=float(r.get("min_interval", 0.0)),
                max_retries=int(r.get("max_retries", 5)),
                backoff_base=float(r.get("backoff_base", 0.5)),
                default_currency=str(r.get("default_currency", "USD")),
            )
        except KeyError as e:
            raise ValueError(f"{path}: 第 {i} 个来源缺少字段 {e}") from e
        if cfg.type not in ("json", "html"):
            raise ValueError(f"{path}: 来源 {cfg.name!r} 的 type 必须是 'json' 或 'html'")
        if cfg.name in seen:
            raise ValueError(f"{path}: 来源名重复 {cfg.name!r}")
        seen.add(cfg.name)
        configs.append(cfg)
    return configs
