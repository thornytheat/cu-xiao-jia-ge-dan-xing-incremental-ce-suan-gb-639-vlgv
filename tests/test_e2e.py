"""端到端：真实样例走完采信 → 归价 → 质量 → 弹性 → 增量 → 报告，再导出。

覆盖一条失败输入（截断文件 422）与两种空态结论，还有幂等重跑。
"""
from __future__ import annotations

import json
from decimal import Decimal

from fixtures import dataset as fx

PAGES = ("/", "/contract", "/quarantine", "/elasticity", "/lift", "/design",
         "/report")


def test_full_pipeline_end_to_end(client):
    response = client.post("/api/import", json={"dataset": "main"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["replay"] is False
    counters = body["counters"]
    assert int(counters["records_total"]) == fx.SALES_RECORDS
    assert int(counters["rows_ok"]) == fx.EXPECTED_ROWS_OK
    assert Decimal(counters["lift_gmv"]) == Decimal(fx.EXPECTED_LIFT_GMV)
    assert counters["grade"] == fx.EXPECTED_GRADE

    quarantine = client.get("/api/quarantine").json()
    codes = {row["code"] for row in quarantine["distribution"]}
    assert {"E_DECODE_FALLBACK", "P_SENTINEL_QTY", "P_PRICE_LOW", "Q_DUP_DAY",
            "Q_MISSING_DAY", "Q_PROMO_OVERLAP"} <= codes

    elastic = client.get("/api/elasticity").json()
    slopes = {row["group_key"]: row["slope"] for row in elastic["rows"]}
    assert Decimal(slopes["MY-0012/电商"]) < 0

    lift = client.get("/api/lift").json()
    assert [row["promo_id"] for row in lift["lift"]] == ["P2601", "P2602"]

    report = client.get("/api/report").json()
    assert report["balanced"] is True
    assert len(report["reconcile"]) == 5

    csv_body = client.get("/api/export?kind=reconcile")
    assert csv_body.status_code == 200
    assert csv_body.text.startswith("\ufeff")
    assert "平" in csv_body.text


def test_truncated_import_rejected_with_reason(client):
    response = client.post("/api/import", json={"dataset": "truncated"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "E_TRUNCATED_TAIL"
    assert payload["level"] == "error"
    assert payload["detail"]["file"] == fx.TRUNCATED_FILE
    assert payload["hint"]
    # 原子性：被拒绝的导入不留半批数据
    assert client.get("/api/batches").json()["count"] == 0


def test_unknown_dataset_rejected(client):
    response = client.post("/api/import", json={"dataset": "no-such-set"})
    assert response.status_code == 422
    assert response.json()["error"] == "E_BAD_REQUEST"
    assert "no-such-set" in response.json()["message"]


def test_empty_and_header_only_are_distinct_results(client):
    empty = client.post("/api/import", json={"dataset": "empty"}).json()
    header_only = client.post("/api/import", json={"dataset": "header_only"}).json()
    assert empty["status"] == "done"
    assert int(empty["counters"]["records_total"]) == 0
    assert int(header_only["counters"]["records_total"]) == 0
    assert empty["batch_id"] != header_only["batch_id"]
    report = client.get("/api/report").json()
    codes = {row["code"] for row in report["distribution"]}
    assert "E_HEADER_ONLY" in codes
    assert "E_EMPTY_FILE" not in codes


def test_import_replay_is_idempotent(client):
    first = client.post("/api/import", json={"dataset": "main"}).json()
    second = client.post("/api/import", json={"dataset": "main"}).json()
    assert first["batch_id"] == second["batch_id"]
    assert second["replay"] is True
    assert client.get("/api/batches").json()["count"] == 1


def test_pages_render_with_data(client_run):
    for path in PAGES:
        response = client_run.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"]
        assert "牧原乳品" in response.text
    home = client_run.get("/").text
    assert "促销弹性与增量测算" in home
    quarantine = client_run.get("/quarantine").text
    assert "严重度" in quarantine
    report = client_run.get("/report").text
    assert "账目核对" in report


def test_quarantine_export_roundtrip(client_run):
    response = client_run.get("/api/quarantine.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = [line for line in response.text.split("\r\n") if line]
    assert lines[0] == "原因码,级别,阶段,来源,行号,字段,原值,说明"
    assert len(lines) > 10
    assert fx.MISSING_DAY in response.text


def test_health_and_datasets(client):
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["rule_version"]
    sets = client.get("/api/datasets").json()
    keys = [item["key"] for item in sets["datasets"]]
    assert keys == ["main", "utf8bom", "truncated", "header_only", "empty"]
    assert sets["zero_modes"] == ["shift", "drop"]


def test_contract_and_price_preview(client):
    contract = client.get("/api/contract").json()
    assert contract["price_unit"] == "元/件"
    assert any(row["code"] == "P_PRICE_ZERO" for row in contract["reasons"])
    preview = client.post("/api/price/preview",
                          json={"price": "1,234.50", "unit": "元/箱", "tax": "13%"})
    assert preview.status_code == 200
    assert preview.json()["price_incl"] == "102.8750"
    assert preview.json()["price_excl"] == "91.0398"
    bad = client.post("/api/price/preview",
                      json={"price": "N/A", "unit": "元/件", "tax": "0.13"})
    assert bad.json()["ok"] is False
    assert bad.json()["code"] == "P_SENTINEL_PRICE"
