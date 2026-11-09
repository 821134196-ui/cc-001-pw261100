"""中断恢复：从检查点续采，不漏采、不重复写入。"""
from collector.engine import Engine


def test_resume_from_checkpoint_after_interruption(db, mock):
    src = mock.json_source(concurrency=1)

    # 第一次运行：3 页只采了 2 页就中断
    r1 = Engine(db, [src], page_limit=2).run()["json-src"]
    assert r1.status == "interrupted"
    assert r1.pages == 2
    assert db.count_products("json-src") == 20
    assert db.get_checkpoint("json-src") == "2"

    # 全新引擎从检查点续采：只抓剩余的第 3 页
    r2 = Engine(db, [src]).run()["json-src"]
    assert r2.status == "ok"
    assert r2.pages == 1
    assert db.count_products("json-src") == 25
    assert db.get_checkpoint("json-src") is None

    ids = [r["product_id"] for r in db.iter_products()]
    assert len(ids) == len(set(ids)) == 25  # 不漏采、不重复

    # 完整重采一遍：upsert 幂等，不产生重复行、不膨胀价格历史
    r3 = Engine(db, [src]).run()["json-src"]
    assert r3.status == "ok"
    assert db.count_products("json-src") == 25
    assert db.count_history() == 25


def test_resume_html_source(db, mock):
    src = mock.html_source()

    r1 = Engine(db, [src], page_limit=1).run()["html-src"]
    assert r1.status == "interrupted"
    assert db.count_products("html-src") == 8
    checkpoint = db.get_checkpoint("html-src")
    assert checkpoint and "page=2" in checkpoint  # 检查点指向下一个待抓页

    r2 = Engine(db, [src]).run()["html-src"]
    assert r2.status == "ok"
    assert db.count_products("html-src") == 20
    assert db.get_checkpoint("html-src") is None
    ids = [r["product_id"] for r in db.iter_products()]
    assert len(ids) == len(set(ids)) == 20
