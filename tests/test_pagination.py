"""分页采集：JSON 接口和 HTML 列表都能翻页采全。"""
from collector.engine import Engine


def test_json_pagination_collects_all_pages(db, mock):
    result = Engine(db, [mock.json_source()]).run()["json-src"]
    assert result.status == "ok"
    assert result.pages == 3  # 25 个商品 / 每页 10
    assert result.items == 25
    assert db.count_products("json-src") == 25
    assert db.get_checkpoint("json-src") is None  # 完成后检查点已清除

    p = db.get_product("json-src", "J001")
    assert p["title"] == "Product J001"
    assert p["category"] == "Home"
    assert p["price_cents"] == 1099  # 9.99 + 1
    assert p["currency"] == "USD"
    assert p["in_stock"] == 1
    assert p["source_updated_at"] == "2026-11-02T08:00:00+00:00"


def test_html_pagination_collects_all_pages(db, mock):
    result = Engine(db, [mock.html_source()]).run()["html-src"]
    assert result.status == "ok"
    assert result.pages == 3  # 20 个商品 / 每页 8
    assert result.items == 20
    assert db.count_products("html-src") == 20
    assert db.get_checkpoint("html-src") is None

    p = db.get_product("html-src", "H003")
    assert p["title"] == "Product H003"
    assert p["price_cents"] == 1299  # $12.99，币种从 $ 符号解析
    assert p["currency"] == "USD"
    assert p["in_stock"] == 1

    out_of_stock = db.get_product("html-src", "H005")  # 5 % 5 == 0 → 缺货
    assert out_of_stock["in_stock"] == 0
