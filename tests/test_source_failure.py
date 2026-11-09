"""来源解析失败：失败来源被标记，其他来源照常完成。"""
from collector.engine import Engine


def test_broken_json_source_fails_but_others_continue(db, mock):
    broken = mock.json_source(name="broken", url=f"{mock.base}/broken-json/products")
    good = mock.json_source(name="good")
    results = Engine(db, [broken, good]).run()

    assert results["broken"].status == "failed"
    assert "ParseError" in results["broken"].error
    assert results["good"].status == "ok"

    assert db.count_products("broken") == 0
    assert db.count_products("good") == 25

    runs = {r["source"]: r for r in db.last_runs()}
    assert runs["broken"]["status"] == "failed"
    assert runs["broken"]["error"]
    assert runs["good"]["status"] == "ok"


def test_broken_html_source_fails_but_others_continue(db, mock):
    broken = mock.html_source(name="broken-html", url=f"{mock.base}/broken-html/products")
    good = mock.html_source(name="good-html")
    results = Engine(db, [broken, good]).run()

    assert results["broken-html"].status == "failed"
    assert "ParseError" in results["broken-html"].error
    assert results["good-html"].status == "ok"
    assert db.count_products("good-html") == 20
