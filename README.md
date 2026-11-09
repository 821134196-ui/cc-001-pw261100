# 本地商品采集工具

从**分页 JSON 接口**和 **HTML 商品列表**采集商品数据，统一存储到本地 SQLite。
纯 Python 标准库实现（运行无需第三方依赖），配套本地模拟数据源，不访问任何真实网站。

## 功能

- **统一存储**：来源、商品 ID、标题、分类、价格、币种、库存状态、来源更新时间
- **去重建档**：同一来源同一商品重复采集时更新信息，不重复建档
- **价格历史**：价格变化时更新现价并保留全部历史价格（`price_history` 表）
- **限流与重试**：每个来源可独立配置并发数与请求间隔；遇到 429、503 或临时网络错误时，
  优先按 `Retry-After` 重试，否则指数退避（可配置次数与基数）
- **断点续采**：每页数据 upsert 与检查点推进在同一事务提交；中断后从检查点继续，
  不漏采、不重复写入
- **故障隔离**：每个来源独立线程采集，单个来源失败不影响其他来源

## 快速开始

```bash
# 1. 启动本地模拟数据源（JSON 接口 + HTML 列表，可选故障注入）
python -m collector serve-mock --port 8080 --flaky-429 2

# 2. 另开终端，启动采集（可复制 sources.example.json 修改）
python -m collector collect --config sources.example.json --db collector.db

# 3. 查看状态
python -m collector status --db collector.db

# 4. 导出数据（CSV 或 JSON；--history 导出价格历史）
python -m collector export --db collector.db --format csv --out products.csv
python -m collector export --db collector.db --format json --out history.json --history
```

演练中断续采：`collect --page-limit 2` 让来源采 2 页即中断（状态为 `interrupted`，
检查点保留），再次执行 `collect` 即从检查点续采。

## 来源配置（sources.json）

```json
{
  "sources": [
    {
      "name": "mock-json",          // 来源名（唯一）
      "type": "json",               // json=分页接口 / html=商品列表页
      "url": "http://127.0.0.1:8080/json/products",
      "page_size": 10,              // 每页条数（仅 json）
      "concurrency": 3,             // 该来源最大并发请求数
      "min_interval": 0.2,          // 该来源两次请求的最小间隔（秒）
      "max_retries": 5,             // 429/503/网络错误最大重试次数
      "backoff_base": 0.5,          // 指数退避基数（秒）
      "default_currency": "USD"     // 页面未标明币种时的默认币种
    }
  ]
}
```

## 数据表

| 表 | 内容 |
|---|---|
| `products` | 商品当前状态，主键 `(source, product_id)`，upsert 幂等 |
| `price_history` | 每次建档/变价追加一条历史价格 |
| `checkpoints` | 未完成来源的续采游标（JSON=已完成的连续页码，HTML=下一个待抓 URL） |
| `runs` | 每次采集运行的状态、页数、条数、错误信息 |

## 模拟数据源端点

| 端点 | 行为 |
|---|---|
| `/json/products?page=N&size=M` | 分页 JSON 接口 |
| `/html/products?page=N` | HTML 商品列表（next 链接翻页） |
| `/flaky/products?...` | 前 N 次 429（带 Retry-After）/ 503，之后正常 |
| `/drop/products?...` | 前 N 次直接断开连接，之后正常 |
| `/broken-json/products`、`/broken-html/products` | 返回无法解析的内容 |

## 测试

```bash
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -v
```

覆盖：分页采集、重复商品去重、价格变化留痕、429/503/断连重试、
频率与并发控制、中断续采（JSON 与 HTML）、来源解析失败故障隔离、CLI 端到端。
