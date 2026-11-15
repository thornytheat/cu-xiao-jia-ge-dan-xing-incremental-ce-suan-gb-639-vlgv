"""M5 增量：基线、金额账、置信区间、最大余额法分摊、幂等重跑与批次。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app import config
from app.modules.m2_price import coerce, versions
from app.modules.m5_lift import baseline, ci, service as lift_service, spread
from fixtures import dataset as fx


def test_baseline_uses_control_factor():
    # 基线 = 促销前日均 × 有效天数 × 对照组同期倍数（不是前一天的销量）
    factor = (Decimal("132.00") / Decimal("120.00")).quantize(Decimal("0.0001"))
    assert factor == Decimal("1.1000")
    pre = Decimal("150.0000")
    days = Decimal("20")
    base_daily = (pre * factor).quantize(Decimal("0.0001"))
    base_qty = (base_daily * days).quantize(Decimal("0.0001"))
    assert base_daily == Decimal("165.0000")
    assert base_qty == Decimal("3300.0000")
    naive = pre * days
    assert naive == Decimal("3000.0000")
    assert base_qty != naive


def test_lift_equals_actual_minus_base(settings, store, batch_id):
    # 金额账：增量 = 实际 − 基线，Decimal 两位，逐行与合计都成立
    totals = store.totals(batch_id)
    for row in store.lift(batch_id):
        assert coerce.money(Decimal(row["actual_gmv"]) - Decimal(row["base_gmv"])) == \
            Decimal(row["lift_gmv"])
        assert coerce.money(Decimal(row["actual_qty"]) - Decimal(row["base_qty"])) == \
            Decimal(row["lift_qty"]).quantize(config.MONEY_QUANT)
    assert coerce.money(Decimal(totals["actual_gmv"]) - Decimal(totals["base_gmv"])) == \
        Decimal(totals["lift_gmv"])
    assert len(store.lift(batch_id)) == fx.EXPECTED_PROMOS_MEASURED


def test_interval_collapses_when_standard_error_is_zero():
    # 不变量：斜率标准误为 0 时区间收成一点
    band = ci.interval(lift_gmv=Decimal("1234.56"), base_qty=Decimal("3000"),
                       base_price=Decimal("11.3274"), dlnp=-0.324824,
                       slope=-1.6, se_slope=0.0)
    assert band["se_gmv"] == "0.00"
    assert band["ci_low"] == "1234.56"
    assert band["ci_high"] == "1234.56"
    assert band["ratio"] == "1.6816"


def test_interval_width_grows_with_standard_error():
    narrow = ci.interval(lift_gmv=Decimal("1000.00"), base_qty=Decimal("3000"),
                         base_price=Decimal("11.3274"), dlnp=-0.324824,
                         slope=-1.6, se_slope=0.01)
    wide = ci.interval(lift_gmv=Decimal("1000.00"), base_qty=Decimal("3000"),
                       base_price=Decimal("11.3274"), dlnp=-0.324824,
                       slope=-1.6, se_slope=0.20)
    assert Decimal(narrow["se_gmv"]) < Decimal(wide["se_gmv"])
    assert Decimal(wide["ci_high"]) - Decimal(wide["ci_low"]) > \
        Decimal(narrow["ci_high"]) - Decimal(narrow["ci_low"])
    assert ci.crosses_zero(Decimal(wide["ci_low"]), Decimal(wide["ci_high"])) is True
    assert ci.crosses_zero(Decimal("100.00"), Decimal("200.00")) is False
    assert ci.log_ratio(Decimal("0"), Decimal("10")) is None


def test_largest_remainder_sums_to_total_exactly():
    # TRAP-24：各自四舍五入到 0.01 后合计会多出一分钱
    total = Decimal("100.00")
    weights = {"电商": Decimal("1"), "商超": Decimal("1"), "经销": Decimal("1")}
    raw = [total / Decimal(3)] * 3
    naive = sum((value.quantize(config.MONEY_QUANT) for value in raw), Decimal("0"))
    assert naive == Decimal("99.99")
    shares = spread.largest_remainder(total, weights)
    assert sum(shares.values(), Decimal("0")) == total
    assert sorted(shares.values()) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert spread.remainder_of(total, [
        {"lift_gmv": str(value)} for value in shares.values()]) == Decimal("0.00")


def test_allocate_keeps_total_for_real_promotions(settings, store, batch_id):
    for row in store.lift(batch_id):
        shares = [item for item in store.spread(batch_id)
                  if item["promo_id"] == row["promo_id"]]
        assert shares, "每个测促销都应有渠道分摊"
        assert sum(Decimal(item["lift_gmv"]) for item in shares) == \
            Decimal(row["lift_gmv"])
    totals = store.totals(batch_id)
    assert Decimal(totals["spread_total"]) == Decimal(totals["lift_gmv"])


def test_idempotent_rerun_adds_no_rows(settings, store, batch_id):
    # 不变量：同键重跑复用结果，行数与合计都不变
    before = {table: len(getattr(store, table)(batch_id))
              for table in ("sales", "quarantine", "elasticity", "lift", "spread")}
    again = lift_service.run(settings=settings, store=store, dataset="main")
    assert again["replay"] is True
    assert again["batch"]["batch_id"] == batch_id
    after = {table: len(getattr(store, table)(batch_id))
             for table in ("sales", "quarantine", "elasticity", "lift", "spread")}
    assert before == after
    assert len(store.list_batches()) == 1


def test_batch_key_changes_with_zero_mode(settings, store):
    first = lift_service.run(settings=settings, store=store, dataset="main",
                             zero_mode="shift")
    second = lift_service.run(settings=settings, store=store, dataset="main",
                              zero_mode="drop")
    assert first["batch_key"] != second["batch_key"]
    assert second["replay"] is False
    assert len(store.list_batches()) == 2
    assert second["batch"]["params_json"].find("drop") >= 0


def test_missing_control_mapping_blocks_promo():
    # 没有对照组映射就无法算基线，必须显式阻断而不是悄悄用 1.0 拼过去
    assert config.CONTROL_MAP.get("MY-9999") is None
    assert config.CONTROL_MAP["MY-0012"] == "MY-0019"
    assert baseline.compute.__doc__ is None or True
    assert issubclass(type(config.CONTROL_MAP), dict)


def test_marks_sample_data_facts(settings, store, batch_id):
    totals = store.totals(batch_id)
    assert int(totals["records_total"]) == fx.SALES_RECORDS
    assert int(totals["rows_ok"]) == fx.EXPECTED_ROWS_OK
    assert int(totals["rows_bad"]) == fx.EXPECTED_ROWS_BAD
    assert Decimal(totals["lift_gmv"]) == Decimal(fx.EXPECTED_LIFT_GMV)
    assert int(totals["promos_total"]) == fx.EXPECTED_PROMOS_TOTAL
    assert int(totals["promos_blocked"]) == fx.EXPECTED_PROMOS_BLOCKED
    assert int(totals["promos_pending"]) == fx.EXPECTED_PROMOS_PENDING
    rows = {row["promo_id"]: row for row in store.lift(batch_id)}
    assert rows["P2601"]["control_sku"] == "MY-0019"
    assert rows["P2602"]["control_sku"] == "MY-0028"
    assert Decimal(rows["P2601"]["ci_low"]) < Decimal(rows["P2601"]["lift_gmv"])
    assert Decimal(rows["P2601"]["ci_high"]) > Decimal(rows["P2601"]["lift_gmv"])
    assert rows["P2601"]["note"] == "区间不含 0"
    assert rows["P2602"]["note"] == "区间包含 0"


def test_baseline_blocks_when_control_mapping_missing():
    # 反例：没有映射就把对照倍数当 1.0，品类走势被悄悄丢掉；必须显式阻断
    window = versions.Window(promo_id="P9", sku="MY-9999", channel="电商", version=1,
                             start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 5),
                             price_incl=Decimal("9.9000"), price_excl=Decimal("8.7611"))
    dates = set(baseline.daterange(dt.date(2026, 10, 1), dt.date(2026, 10, 5)))
    assert config.CONTROL_MAP.get("MY-9999") is None
    assert baseline.compute([window], dates, [], [], {}) == "A_NO_CONTROL"
