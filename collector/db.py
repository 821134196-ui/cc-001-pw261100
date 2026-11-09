"""SQLite 存储层：商品、价格历史、采集检查点、运行记录。

关键设计：每一页的商品 upsert 与检查点推进在同一个事务中提交，
因此崩溃只会导致整页重抓（upsert 幂等），不会漏采也不会重复建档。
"""
from __future__ import annotations

import sqlite3
import threading

from .models import RawItem, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    source            TEXT NOT NULL,
    product_id        TEXT NOT NULL,
    title             TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT '',
    price_cents       INTEGER NOT NULL,
    currency          TEXT NOT NULL DEFAULT '',
    in_stock          INTEGER NOT NULL DEFAULT 0,
    source_updated_at TEXT NOT NULL DEFAULT '',
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    PRIMARY KEY (source, product_id)
);
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    product_id  TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    currency    TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    source     TEXT PRIMARY KEY,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL DEFAULT 'running',
    pages_done     INTEGER NOT NULL DEFAULT 0,
    items_upserted INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
"""


class Database:
    """线程安全的 SQLite 封装（所有写操作串行化，单连接）。"""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def save_page(self, source: str, items: list[RawItem], cursor: str | None) -> None:
        """一页商品 upsert + 检查点推进，同一事务提交。

        cursor: None 表示不动检查点；"" 表示清除（该来源已采完）；
        其他值表示推进到该游标。
        """
        now = utcnow_iso()
        with self._lock, self._conn:
            for it in items:
                self._upsert_one(source, it, now)
            if cursor == "":
                self._conn.execute("DELETE FROM checkpoints WHERE source = ?", (source,))
            elif cursor is not None:
                self._conn.execute(
                    "INSERT INTO checkpoints (source, cursor, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(source) DO UPDATE SET cursor = excluded.cursor, "
                    "updated_at = excluded.updated_at",
                    (source, cursor, now),
                )

    def _upsert_one(self, source: str, it: RawItem, now: str) -> None:
        row = self._conn.execute(
            "SELECT price_cents, currency FROM products WHERE source = ? AND product_id = ?",
            (source, it.product_id),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO products (source, product_id, title, category, price_cents,"
                " currency, in_stock, source_updated_at, first_seen_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, it.product_id, it.title, it.category, it.price_cents, it.currency,
                 int(it.in_stock), it.source_updated_at, now, now),
            )
            self._add_history(source, it, now)
        else:
            self._conn.execute(
                "UPDATE products SET title = ?, category = ?, price_cents = ?, currency = ?,"
                " in_stock = ?, source_updated_at = ?, last_seen_at = ?"
                " WHERE source = ? AND product_id = ?",
                (it.title, it.category, it.price_cents, it.currency, int(it.in_stock),
                 it.source_updated_at, now, source, it.product_id),
            )
            if row["price_cents"] != it.price_cents or row["currency"] != it.currency:
                self._add_history(source, it, now)

    def _add_history(self, source: str, it: RawItem, now: str) -> None:
        self._conn.execute(
            "INSERT INTO price_history (source, product_id, price_cents, currency, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (source, it.product_id, it.price_cents, it.currency, now),
        )

    def start_run(self, source: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (source, started_at) VALUES (?, ?)", (source, utcnow_iso())
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, pages: int, items: int,
                   error: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, pages_done = ?,"
                " items_upserted = ?, error = ? WHERE id = ?",
                (utcnow_iso(), status, pages, items, error, run_id),
            )

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def get_checkpoint(self, source: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cursor FROM checkpoints WHERE source = ?", (source,)
            ).fetchone()
            return row["cursor"] if row else None

    def clear_checkpoint(self, source: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM checkpoints WHERE source = ?", (source,))

    def count_products(self, source: str | None = None) -> int:
        with self._lock:
            if source:
                return self._conn.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE source = ?", (source,)
                ).fetchone()["n"]
            return self._conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]

    def count_history(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS n FROM price_history").fetchone()["n"]

    def get_product(self, source: str, product_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM products WHERE source = ? AND product_id = ?",
                (source, product_id),
            ).fetchone()

    def product_counts(self) -> dict[str, int]:
        with self._lock:
            return {r["source"]: r["n"] for r in self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM products GROUP BY source ORDER BY source")}

    def last_runs(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT r.* FROM runs r JOIN (SELECT source, MAX(id) AS mid FROM runs"
                " GROUP BY source) m ON r.id = m.mid ORDER BY r.source"
            ).fetchall()

    def pending_checkpoints(self) -> dict[str, str]:
        with self._lock:
            return {r["source"]: r["cursor"] for r in self._conn.execute(
                "SELECT source, cursor FROM checkpoints ORDER BY source")}

    def iter_products(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM products ORDER BY source, product_id").fetchall()

    def iter_price_history(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM price_history ORDER BY source, product_id, id").fetchall()
