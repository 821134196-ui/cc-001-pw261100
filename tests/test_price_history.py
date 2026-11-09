"""价格变化：商品现价更新，历史价格保留。"""
from collector.engine import Engine


def test_price_change_keeps_history(db, mock):
    src = mock.json_source()
    Engine(db, [src]).run()
    assert db.count_history() == 25  # 每个商品建档时记录初始价格

    # J002 价格 11.99 → 1.99
    with mock.state.lock:
        mock.state.json_products[1]["price"] = "1.99"
    Engine(db, [src]).run()

    p = db.get_product("json-src", "J002")
    assert p["price_cents"] == 199  # 现价已更新

    hist = [r for r in db.iter_price_history() if r["product_id"] == "J002"]
    assert [h["price_cents"] for h in hist] == [1199, 199]  # 历史完整保留
    assert all(h["currency"] == "USD" for h in hist)

    # 未变价的商品不产生新的历史记录
    assert db.count_history() == 26

    # 再采一次（价格不再变化）：历史不膨胀
    Engine(db, [src]).run()
    assert db.count_history() == 26
