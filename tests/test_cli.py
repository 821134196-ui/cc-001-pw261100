"""CLI 端到端：collect → status → export。"""
import csv
import json

from collector.cli import main


def test_cli_collect_status_export(tmp_path, mock, capsys):
    db_file = str(tmp_path / "cli.db")
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps({"sources": [
        {"name": "cli-json", "type": "json", "url": f"{mock.base}/json/products",
         "page_size": 10, "backoff_base": 0.01},
        {"name": "cli-html", "type": "html", "url": f"{mock.base}/html/products",
         "backoff_base": 0.01},
    ]}), encoding="utf-8")

    # collect
    assert main(["collect", "--config", str(cfg), "--db", db_file]) == 0
    out = capsys.readouterr().out
    assert "cli-json: ok" in out
    assert "cli-html: ok" in out

    # status
    assert main(["status", "--db", db_file]) == 0
    out = capsys.readouterr().out
    assert "cli-json" in out and "25" in out
    assert "cli-html" in out and "20" in out

    # export csv
    csv_out = tmp_path / "products.csv"
    assert main(["export", "--db", db_file, "--format", "csv",
                 "--out", str(csv_out)]) == 0
    with csv_out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 45
    assert rows[0]["source"] == "cli-html"  # 按来源排序
    assert {"source", "product_id", "title", "category", "price", "currency",
            "in_stock", "source_updated_at"} <= set(rows[0])

    # export json
    json_out = tmp_path / "products.json"
    assert main(["export", "--db", db_file, "--format", "json",
                 "--out", str(json_out)]) == 0
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(data) == 45
    j001 = next(r for r in data if r["product_id"] == "J001")
    assert j001["price"] == "10.99"
    assert j001["currency"] == "USD"
    assert j001["in_stock"] is True

    # export 价格历史
    hist_out = tmp_path / "history.csv"
    assert main(["export", "--db", db_file, "--format", "csv",
                 "--out", str(hist_out), "--history"]) == 0
    with hist_out.open(encoding="utf-8") as f:
        hist = list(csv.DictReader(f))
    assert len(hist) == 45
    assert set(hist[0]) == {"source", "product_id", "price", "currency", "recorded_at"}


def test_cli_collect_unknown_source(tmp_path, mock, capsys):
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps({"sources": [
        {"name": "s1", "type": "json", "url": f"{mock.base}/json/products"},
    ]}), encoding="utf-8")
    rc = main(["collect", "--config", str(cfg), "--db", str(tmp_path / "x.db"),
               "--source", "nope"])
    assert rc == 2
    assert "nope" in capsys.readouterr().err
