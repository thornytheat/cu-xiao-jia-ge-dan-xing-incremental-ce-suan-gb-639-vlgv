"""M3 质量：重复日、缺日、天数账守恒、覆盖门槛、未知商品、导出可复核。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config, reasons
from app.modules.m3_quality import metrics, quarantine, rules, service as quality_service
from fixtures import dataset as fx


def _row(day, channel="电商", sku="MY-0012", qty=100, price=Decimal("12.80"),
         line_no=None):
    from app.modules.m2_price.service import SaleRow
    if line_no is None:
        line_no = int(day.replace("-", "")) % 10000
    return SaleRow(line_no=line_no, sale_date=dt.date.fromisoformat(day),
                   channel=channel, sku=sku, sku_name="测试", price_incl=price,
                   price_excl=price, tax_rate=Decimal("0.13"), qty=qty, promo_id="",
                   price_unit="元/件", amount_excl=(price * Decimal(qty)).quantize(
                       config.MONEY_QUANT))


def test_duplicate_day_keeps_first_line():
    # TRAP-14：同键两条不同销量，后写覆盖会让增量凭空变化
    rows = [_row("2026-10-14", qty=134, line_no=7),
            _row("2026-10-14", qty=127, line_no=9),
            _row("2026-10-15", line_no=11)]
    kept, dropped = rules.split_duplicates(rows)
    assert len(kept) == 2
    assert [row.qty for row in kept] == [134, 100]
    assert kept[0].line_no == 7
    assert len(dropped) == 1
    assert dropped[0]["code"] == "Q_DUP_DAY"
    assert dropped[0]["line_no"] == 9


def test_missing_day_counted_separately(settings, store, batch_id):
    # 缺日不能当 0 销量：P2601 促销窗内 2026-10-18 没有记录
    rows = quarantine.enrich(store.quarantine(batch_id))
    missing = [row for row in rows if row["code"] == "Q_MISSING_DAY"]
    assert len(missing) == 1
    assert missing[0]["raw_value"] == fx.MISSING_DAY
    assert missing[0]["level"] == reasons.WARN
    totals = store.totals(batch_id)
    assert int(totals["days_missing"]) == fx.EXPECTED_DAYS_MISSING


def test_day_account_identity_holds(settings, store, batch_id):
    # 不变量：促销天数 = 有效 + 缺日 + 剔除
    totals = store.totals(batch_id)
    assert int(totals["days_expected"]) == (
        int(totals["days_valid"]) + int(totals["days_missing"])
        + int(totals["days_dropped"]))
    assert int(totals["days_expected"]) == fx.EXPECTED_DAYS
    assert int(totals["days_valid"]) == fx.EXPECTED_DAYS_VALID
    accounts = {}
    for row in store.lift(batch_id):
        accounts[row["promo_id"]] = (row["days"], row["days_valid"])
    assert accounts == {"P2601": (20, 18), "P2602": (14, 14)}


def test_low_coverage_blocks_measurement():
    accounts = [{"promo_id": "P1", "sku": "MY-0012", "channel": "电商",
                 "days": 10, "days_valid": 5, "days_missing": 5, "days_dropped": 0,
                 "coverage": Decimal("0.5000")}]
    rows = rules.low_coverage_quarantine(accounts)
    assert len(rows) == 1
    assert rows[0]["code"] == "Q_LOW_COVERAGE"
    assert quality_service.measured_accounts(accounts) == []
    assert Decimal("0.5") < config.MIN_COVERAGE_RATIO


def test_unknown_promo_sku_flagged():
    class Window:
        promo_id = "P9"
        sku = "MY-9999"
        channel = "电商"

    rows = rules.unknown_sku_quarantine([Window()], {("MY-0012", "电商")})
    assert len(rows) == 1
    assert rows[0]["code"] == "Q_PROMO_UNKNOWN_SKU"
    assert "MY-9999" in rows[0]["raw_value"]


def test_quarantine_csv_carries_reason_and_line(settings, store, batch_id):
    rows = quarantine.enrich(store.quarantine(batch_id))
    text = quarantine.to_csv(rows)
    lines = [line for line in text.split("\r\n") if line]
    assert lines[0] == "原因码,级别,阶段,来源,行号,字段,原值,说明"
    assert len(lines) == len(rows) + 1
    assert "P_SENTINEL_QTY" in text
    assert "Q_DUP_DAY" in text
    assert fx.DUP_DAY in text


def test_level_counts_match_distribution(settings, store, batch_id):
    rows = quarantine.enrich(store.quarantine(batch_id))
    counts = quarantine.level_counts(rows)
    distribution = quarantine.distribution(rows)
    assert sum(item["count"] for item in distribution) == len(rows)
    assert counts[reasons.ERROR] == sum(
        1 for row in rows if row["level"] == reasons.ERROR)
    assert counts[reasons.ERROR] + counts[reasons.WARN] + counts[reasons.INFO] == len(rows)
    stages = {row["stage"] for row in rows}
    assert {"ingest", "price", "quality"} <= stages


def test_grade_and_coverage_from_accounts(settings, store, batch_id):
    totals = store.totals(batch_id)
    assert totals["grade"] == fx.EXPECTED_GRADE
    assert totals["coverage"] == fx.EXPECTED_COVERAGE
    accounts = [{"days": 20, "days_valid": 20, "days_missing": 0, "days_dropped": 0,
                 "coverage": Decimal("1.0000"), "promo_id": "P1", "sku": "A",
                 "channel": "电商"}]
    clean = metrics.score(accounts, [], records_total=100)
    assert clean["grade"] == "A"
    assert clean["balanced"] is True
    dirty = metrics.score(accounts, [
        {"code": "P_PRICE_LOW", "level": reasons.ERROR, "stage": "price"} for _ in range(9)
    ], records_total=100)
    assert dirty["grade"] == "C"
    assert dirty["data_errors"] == 9
    assert dirty["bad_rate"] == "0.0900"


def test_coverage_gate_is_inclusive_at_threshold():
    # 边界：有效天数占比正好等于门槛仍进测算，少一天就阻断（>= 不是 >）
    def account(valid: int, coverage: str) -> dict:
        return {"promo_id": "P1", "sku": "MY-0012", "channel": "电商", "days": 10,
                "days_valid": valid, "days_missing": 10 - valid, "days_dropped": 0,
                "coverage": Decimal(coverage)}

    at_gate = account(7, "0.7000")
    below = account(6, "0.6000")
    assert Decimal(at_gate["coverage"]) == config.MIN_COVERAGE_RATIO
    assert quality_service.measured_accounts([at_gate]) == [at_gate]
    assert quality_service.measured_accounts([below]) == []
    assert quality_service.measured_accounts([at_gate, below]) == [at_gate]
    blocked = rules.low_coverage_quarantine([below])
    assert [row["code"] for row in blocked] == ["Q_LOW_COVERAGE"]
    assert "P1" in blocked[0]["detail"]
    assert rules.low_coverage_quarantine([at_gate]) == []
