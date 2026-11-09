"""命令行：collect（启动采集）/ status（查看状态）/ export（导出数据）/ serve-mock（模拟数据源）。"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from .config import load_sources
from .db import Database
from .engine import Engine
from .mock_server import MockState, create_mock_server

PRODUCT_FIELDS = ["source", "product_id", "title", "category", "price", "currency",
                  "in_stock", "source_updated_at", "first_seen_at", "last_seen_at"]
HISTORY_FIELDS = ["source", "product_id", "price", "currency", "recorded_at"]


def _fmt_price(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def _product_dict(row) -> dict:
    return {
        "source": row["source"],
        "product_id": row["product_id"],
        "title": row["title"],
        "category": row["category"],
        "price": _fmt_price(row["price_cents"]),
        "currency": row["currency"],
        "in_stock": bool(row["in_stock"]),
        "source_updated_at": row["source_updated_at"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
    }


def _history_dict(row) -> dict:
    return {
        "source": row["source"],
        "product_id": row["product_id"],
        "price": _fmt_price(row["price_cents"]),
        "currency": row["currency"],
        "recorded_at": row["recorded_at"],
    }


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def _cmd_collect(args) -> int:
    sources = load_sources(args.config)
    if args.source:
        unknown = set(args.source) - {s.name for s in sources}
        if unknown:
            print(f"配置中不存在来源: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
    db = Database(args.db)
    try:
        results = Engine(db, sources, page_limit=args.page_limit).run(
            only=set(args.source) or None)
    finally:
        db.close()
    failed = 0
    for name in sorted(results):
        r = results[name]
        line = f"{r.source}: {r.status} (pages={r.pages}, items={r.items})"
        if r.error:
            line += f" error={r.error}"
        print(line)
        if r.status == "failed":
            failed += 1
    return 1 if failed else 0


def _cmd_status(args) -> int:
    db = Database(args.db)
    try:
        counts = db.product_counts()
        runs = {r["source"]: r for r in db.last_runs()}
        checkpoints = db.pending_checkpoints()
    finally:
        db.close()
    sources = sorted(set(counts) | set(runs) | set(checkpoints))
    if not sources:
        print("暂无数据（数据库为空）")
        return 0
    header = (f"{'SOURCE':<14} {'PRODUCTS':>8} {'STATUS':<12} {'ITEMS':>6} "
              f"{'PAGES':>6} {'FINISHED':<21} {'CHECKPOINT':<24} ERROR")
    print(header)
    print("-" * len(header))
    for name in sources:
        r = runs.get(name)
        print(f"{name:<14} {counts.get(name, 0):>8} "
              f"{(r['status'] if r else '-'):<12} "
              f"{(r['items_upserted'] if r else 0):>6} "
              f"{(r['pages_done'] if r else 0):>6} "
              f"{((r['finished_at'] or '-') if r else '-'):<21} "
              f"{checkpoints.get(name, '-'):<24} "
              f"{(r['error'] or '') if r else ''}")
    return 0


def _cmd_export(args) -> int:
    db = Database(args.db)
    try:
        if args.history:
            rows = [_history_dict(r) for r in db.iter_price_history()]
            fields = HISTORY_FIELDS
        else:
            rows = [_product_dict(r) for r in db.iter_products()]
            fields = PRODUCT_FIELDS
    finally:
        db.close()
    if args.format == "json":
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
            f.write("\n")
    else:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f"已导出 {len(rows)} 条记录到 {args.out}")
    return 0


def _cmd_serve_mock(args) -> int:
    state = MockState(json_count=args.json_count, html_count=args.html_count)
    state.flaky_429 = args.flaky_429
    state.flaky_503 = args.flaky_503
    state.drops = args.drop
    server, _ = create_mock_server(state=state, port=args.port)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"模拟数据源已启动: {base}")
    print(f"  JSON 接口: {base}/json/products?page=1&size=10")
    print(f"  HTML 列表: {base}/html/products?page=1")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="collector", description="本地商品采集工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="启动采集")
    p.add_argument("--config", required=True, help="来源配置 JSON 文件")
    p.add_argument("--db", default="collector.db", help="SQLite 数据库路径")
    p.add_argument("--source", action="append", default=[],
                   help="只采集指定来源（可重复）")
    p.add_argument("--page-limit", type=int, default=None,
                   help="每个来源最多抓取的页数（用于演练中断续采）")

    p = sub.add_parser("status", help="查看采集状态")
    p.add_argument("--db", default="collector.db")

    p = sub.add_parser("export", help="导出数据")
    p.add_argument("--db", default="collector.db")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--out", required=True, help="输出文件路径")
    p.add_argument("--history", action="store_true", help="导出价格历史而非商品")

    p = sub.add_parser("serve-mock", help="启动本地模拟数据源")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--json-count", type=int, default=25, help="JSON 接口商品数")
    p.add_argument("--html-count", type=int, default=20, help="HTML 列表商品数")
    p.add_argument("--flaky-429", type=int, default=0, help="前 N 次请求返回 429")
    p.add_argument("--flaky-503", type=int, default=0, help="之后 N 次请求返回 503")
    p.add_argument("--drop", type=int, default=0, help="之后 N 次请求直接断连")

    args = parser.parse_args(argv)
    handler = {
        "collect": _cmd_collect,
        "status": _cmd_status,
        "export": _cmd_export,
        "serve-mock": _cmd_serve_mock,
    }[args.cmd]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
