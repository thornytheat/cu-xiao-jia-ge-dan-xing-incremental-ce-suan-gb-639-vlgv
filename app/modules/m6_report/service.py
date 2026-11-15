"""报告编排与对账：四账逐项核对，差额为 0 才算平账。

四账：
- 行账：数据记录 = 入模行 + 剔除行
- 天数账：促销天数 = 有效 + 缺日 + 剔除
- 金额账：实际销售额 = 基线销售额 + 增量销售额
- 促销账：促销总数 = 已测算 + 被阻断 + 未生效
- 分摊账：增量合计 = 各渠道分摊合计
"""
from __future__ import annotations

from decimal import Decimal

from ... import config, reasons
from ..m2_price import coerce
from ..m3_quality import quarantine as quarantine_mod
from . import csv_out, manifest_out


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def reconcile(store, batch_id: str) -> list[dict]:
    totals = store.totals(batch_id)
    rows: list[dict] = []

    records = int(totals.get("records_total", 0))
    ok = int(totals.get("rows_ok", 0))
    bad = int(totals.get("rows_bad", 0))
    rows.append(_line("行账", records, ok + bad, "数据记录 = 入模行 + 剔除行"))

    days = int(totals.get("days_expected", 0))
    days_sum = (int(totals.get("days_valid", 0)) + int(totals.get("days_missing", 0))
                + int(totals.get("days_dropped", 0)))
    rows.append(_line("天数账", days, days_sum, "促销天数 = 有效 + 缺日 + 剔除"))

    promos = int(totals.get("promos_total", 0))
    promos_sum = (int(totals.get("promos_measured", 0))
                  + int(totals.get("promos_blocked", 0))
                  + int(totals.get("promos_pending", 0)))
    rows.append(_line("促销账", promos, promos_sum,
                      "促销总数 = 已测算 + 被阻断 + 未生效"))

    actual = coerce.money(_dec(totals.get("actual_gmv", "0")))
    composed = coerce.money(_dec(totals.get("base_gmv", "0")) + _dec(totals.get("lift_gmv", "0")))
    rows.append(_money_line("金额账", actual, composed,
                            "实际销售额 = 基线销售额 + 增量销售额"))

    lift = coerce.money(_dec(totals.get("lift_gmv", "0")))
    spread = coerce.money(_dec(totals.get("spread_total", "0")))
    rows.append(_money_line("分摊账", lift, spread, "增量合计 = 各渠道分摊合计"))
    return rows


def _line(account: str, left: int, right: int, note: str) -> dict:
    delta = left - right
    return {
        "account": account,
        "left": str(left),
        "right": str(right),
        "delta": str(delta),
        "verdict": "平" if delta == 0 else "不平",
        "ok": delta == 0,
        "note": note,
    }


def _money_line(account: str, left: Decimal, right: Decimal, note: str) -> dict:
    delta = coerce.money(left - right)
    return {
        "account": account,
        "left": str(left),
        "right": str(right),
        "delta": str(delta),
        "verdict": "平" if delta == 0 else "不平",
        "ok": delta == 0,
        "note": note,
    }


def export_rows(kind: str, store, batch_id: str) -> str:
    if kind == "sales":
        return csv_out.sales_csv(store.sales(batch_id))
    if kind == "elastic":
        return csv_out.elastic_csv(store.elasticity(batch_id))
    if kind == "reconcile":
        return csv_out.reconcile_csv(reconcile(store, batch_id))
    return csv_out.lift_csv(store.lift(batch_id))


def report_payload(store, batch_id: str) -> dict:
    totals = store.totals(batch_id)
    lift_rows = store.lift(batch_id)
    elasticity = store.elasticity(batch_id)
    distribution = quarantine_mod.distribution(
        quarantine_mod.enrich(store.quarantine(batch_id)))
    accounts = reconcile(store, batch_id)
    headline = next((r for r in elasticity
                     if r["scope"] == "all" and r["group_key"] == "全渠道"), None)
    span = None
    if lift_rows:
        span = (min(r["start_date"] for r in lift_rows),
                max(r["end_date"] for r in lift_rows))
    return {
        "batch_id": batch_id,
        "totals": totals,
        "reconcile": accounts,
        "balanced": all(row["ok"] for row in accounts),
        "lift": lift_rows,
        "elasticity": elasticity,
        "headline": headline,
        "distribution": distribution,
        "window": span,
        "money_unit": config.MONEY_UNIT,
        "qty_unit": config.QTY_UNIT,
        "levels": reasons.LEVEL_LABELS,
    }


def export_filename(kind: str, batch_id: str) -> str:
    return f"pricelift-{kind}-{batch_id}.csv"


def export_manifest(store, batch_id: str, fmt: str = "json") -> tuple[str, str]:
    doc = store.manifest(batch_id) or {}
    name = f"pricelift-manifest-{batch_id}.{fmt}"
    if fmt == "csv":
        return name, manifest_out.to_csv(doc)
    return name, manifest_out.to_json(doc)


__all__ = ["reconcile", "report_payload", "export_rows", "export_filename",
           "export_manifest", "csv_out", "manifest_out"]
