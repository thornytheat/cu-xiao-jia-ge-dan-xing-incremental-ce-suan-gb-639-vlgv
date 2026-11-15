"""M6 报告：四账核对、差异识别、CSV 导出、运行清单与失败输入。"""
from __future__ import annotations

import json
from decimal import Decimal

from app import config
from app.modules.m6_report import csv_out, manifest_out, service as report_service
from fixtures import dataset as fx


def test_reconcile_four_accounts_are_balanced(settings, store, batch_id):
    rows = report_service.reconcile(store, batch_id)
    assert [row["account"] for row in rows] == ["行账", "天数账", "促销账", "金额账", "分摊账"]
    for row in rows:
        assert row["verdict"] == "平"
        assert row["ok"] is True
        assert Decimal(row["delta"]) == Decimal("0")
    payload = report_service.report_payload(store, batch_id)
    assert payload["balanced"] is True
    assert payload["totals"]["lift_gmv"] == fx.EXPECTED_LIFT_GMV


def test_reconcile_detects_imbalance(settings, store, batch_id):
    with store.connect() as conn:
        conn.execute("UPDATE totals SET value=? WHERE batch_id=? AND key='rows_bad'",
                     ("99", batch_id))
    rows = report_service.reconcile(store, batch_id)
    line = next(row for row in rows if row["account"] == "行账")
    assert line["verdict"] == "不平"
    assert line["ok"] is False
    assert line["delta"] == "-91"
    payload = report_service.report_payload(store, batch_id)
    assert payload["balanced"] is False


def test_lift_csv_has_header_and_every_row(settings, store, batch_id):
    text = report_service.export_rows("lift", store, batch_id)
    assert text.startswith("\ufeff")
    lines = [line for line in text.lstrip("\ufeff").split("\r\n") if line]
    assert lines[0].startswith("促销编号,商品编码,渠道")
    assert len(lines) == len(store.lift(batch_id)) + 1
    assert "P2601" in text
    assert "区间不含 0" in text


def test_sales_and_elastic_csv_columns(settings, store, batch_id):
    sales = report_service.export_rows("sales", store, batch_id)
    assert sales.lstrip("\ufeff").split("\r\n")[0].startswith("行号,日期,渠道,商品编码")
    elastic = report_service.export_rows("elastic", store, batch_id)
    head = elastic.lstrip("\ufeff").split("\r\n")[0]
    assert head.startswith("范围,分组,样本数")
    lines = [line for line in elastic.lstrip("\ufeff").split("\r\n") if line]
    assert len(lines) == len(store.elasticity(batch_id)) + 1


def test_reconcile_csv_roundtrip(settings, store, batch_id):
    text = report_service.export_rows("reconcile", store, batch_id)
    lines = [line for line in text.lstrip("\ufeff").split("\r\n") if line]
    assert lines[0] == "账目,左值,右值,差额,结论,说明"
    assert len(lines) == len(report_service.reconcile(store, batch_id)) + 1
    assert ",平," in text


def test_manifest_carries_run_id_and_digest(settings, store, batch_id):
    doc = store.manifest(batch_id)
    assert doc["batch_id"] == batch_id
    assert doc["run_id"].startswith("PL-")
    assert len(doc["input_digest"]) == 64
    names = {item["name"] for item in doc["inputs"]}
    assert fx.SALES_FILE in names
    assert doc["rule_version"] == config.RULE_VERSION
    sales_input = next(item for item in doc["inputs"] if item["name"] == fx.SALES_FILE)
    assert sales_input["sha256"] == fx.SALES_SHA256
    assert sales_input["bytes"] == fx.SALES_BYTES
    name, body = report_service.export_manifest(store, batch_id, "json")
    assert name.endswith(".json")
    assert json.loads(body)["batch_id"] == batch_id
    _, csv_body = report_service.export_manifest(store, batch_id, "csv")
    assert "run_id" in csv_body


def test_unknown_export_kind_falls_back_to_lift(settings, store, batch_id):
    text = report_service.export_rows("unknown-kind", store, batch_id)
    assert text.lstrip("\ufeff").split("\r\n")[0].startswith("促销编号")
    assert report_service.export_filename("lift", batch_id) == \
        f"pricelift-lift-{batch_id}.csv"


def test_export_without_batch_is_422(client):
    response = client.get("/api/export?kind=lift")
    assert response.status_code == 422
    assert "先跑一次测算" in response.text
    response = client.get("/api/report")
    assert response.status_code == 200
    assert response.json()["batch_id"] is None


def test_report_payload_distribution_and_window(settings, store, batch_id):
    payload = report_service.report_payload(store, batch_id)
    codes = {row["code"] for row in payload["distribution"]}
    assert {"P_SENTINEL_QTY", "Q_DUP_DAY", "Q_MISSING_DAY"} <= codes
    assert payload["window"] == ("2026-10-05", "2026-10-25")
    assert payload["headline"]["scope"] == "all"
    assert payload["levels"]["error"] == "严重"
    assert csv_out.kind_label("reconcile") == "账目核对"


def test_manifest_json_serializes_decimals_without_float():
    doc = {"amount": Decimal("6685.74"), "nested": [Decimal("0.01")]}
    text = manifest_out.to_json(doc)
    assert '"6685.74"' in text
    assert '"0.01"' in text
    assert isinstance(manifest_out.json_safe(Decimal("1.10")), str)


def test_reconcile_stays_balanced_under_drop_mode(settings, store):
    # 不变量：换一种 0 销量处理方式，五账仍然全部平
    from app.modules.m5_lift import service as lift_service
    result = lift_service.run(settings=settings, store=store, dataset="main",
                              zero_mode="drop")
    other = result["batch"]["batch_id"]
    assert result["replay"] is False
    rows = report_service.reconcile(store, other)
    assert [row["account"] for row in rows] == ["行账", "天数账", "促销账", "金额账", "分摊账"]
    assert [row["verdict"] for row in rows] == ["平"] * 5
    payload = report_service.report_payload(store, other)
    assert payload["balanced"] is True
    assert payload["totals"]["coverage"] != ""
