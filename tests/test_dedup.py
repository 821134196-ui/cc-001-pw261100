"""重复采集：同一来源同一商品更新而不是重复建档。"""
from collector.engine import Engine


def test_recollection_updates_instead_of_duplicating(db, mock):
    src = mock.json_source()
    assert Engine(db, [src]).run()["json-src"].status == "ok"
    assert db.count_products() == 25

    # 来源数据变化：J001 改标题，新增 J999
    with mock.state.lock:
        mock.state.json_products[0]["title"] = "Product J001 (v2)"
        mock.state.json_products.append({
            "id": "J999", "title": "Product J999", "category": "Tools",
            "price": "99.00", "currency": "USD", "in_stock": True,
            "updated_at": "2026-11-10T08:00:00+00:00",
        })

    result = Engine(db, [src]).run()["json-src"]
    assert result.status == "ok"
    assert db.count_products() == 26  # 只新增 1 个，没有重复建档

    rows = [r for r in db.iter_products() if r["product_id"] == "J001"]
    assert len(rows) == 1
    assert rows[0]["title"] == "Product J001 (v2)"  # 信息已更新
    assert rows[0]["last_seen_at"] >= rows[0]["first_seen_at"]

    # 第三次采集（数据无变化）：数量仍然不变
    assert Engine(db, [src]).run()["json-src"].status == "ok"
    assert db.count_products() == 26
