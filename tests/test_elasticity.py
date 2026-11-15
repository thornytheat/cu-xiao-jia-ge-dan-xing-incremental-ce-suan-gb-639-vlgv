"""M4 弹性：对数线性斜率、标准误、0 销量两种处理、退化输入与显著性判定。

期望值由独立的 numpy.polyfit + 手写标准误公式算出，与 app/modules/m4_elastic/ols.py
的实现路径不同，因此可以用作反例对照。
"""
from __future__ import annotations

import datetime as dt
import math
import tempfile
from decimal import Decimal

from app import config
from app.modules.m2_price.service import SaleRow
from app.modules.m4_elastic import fit, ols, prepare, service as elastic_service
from app.modules.m5_lift import service as lift_service
from app.store import Store
from fixtures import dataset as fx

PRICES = tuple(fx.CASES["prices"])
QTYS = tuple(fx.CASES["qtys"])
BASE = fx.CASES["shift_point"]
NAIVE_SLOPE = fx.CASES["naive_uncentered_slope"]


def _row(price, qty, day, sku="MY-0012", channel="电商"):
    value = Decimal(price)
    return SaleRow(line_no=day, sale_date=dt.date(2026, 9, day), channel=channel, sku=sku,
                   sku_name="测试", price_incl=value, price_excl=value,
                   tax_rate=Decimal("0.13"), qty=qty, promo_id="", price_unit="元/件",
                   amount_excl=(value * Decimal(qty)).quantize(Decimal("0.01")))


def _rows():
    return [_row(PRICES[i], QTYS[i], i + 1) for i in range(len(PRICES))]


def _sku(result, key="MY-0012/电商"):
    return fit.pick(result["rows"], "sku", key)


def test_slope_matches_independent_polyfit():
    result = elastic_service.run(_rows(), "shift")
    row = _sku(result)
    assert row is not None
    assert row["n"] == BASE["n"]
    assert row["slope"] == BASE["slope"]
    assert row["intercept"] == BASE["intercept"]
    assert row["se"] == BASE["se"]
    assert row["tvalue"] == BASE["tvalue"]
    assert row["r2"] == BASE["r2"]
    assert row["significant"] == 1
    assert row["note"] == "显著"


def test_zero_qty_shift_keeps_every_point():
    rows = [_row(PRICES[i], (0 if i == 1 else QTYS[i]), i + 1) for i in range(len(PRICES))]
    result = elastic_service.run(rows, "shift")
    row = _sku(result)
    assert row["n"] == 7
    assert row["zero_count"] == 1
    assert row["zero_mode"] == "shift"
    assert row["slope"] == fx.CASES["zero_shifted"]["slope"]
    codes = [item["code"] for item in result["quarantine"]]
    assert "L_ZERO_QTY_SHIFTED" in codes


def test_zero_qty_drop_removes_points():
    rows = [_row(PRICES[i], (0 if i == 1 else QTYS[i]), i + 1) for i in range(len(PRICES))]
    result = elastic_service.run(rows, "drop")
    row = _sku(result)
    assert row["n"] == fx.CASES["zero_dropped"]["n"]
    assert row["zero_count"] == 1
    assert row["zero_mode"] == "drop"
    assert row["slope"] == fx.CASES["zero_dropped"]["slope"]
    codes = [item["code"] for item in result["quarantine"]]
    assert "L_ZERO_QTY_DROPPED" in codes
    assert "L_ZERO_QTY_SHIFTED" not in codes


def test_no_price_variation_blocked():
    rows = [_row("12.80", 100 + i, i + 1) for i in range(8)]
    result = elastic_service.run(rows, "shift")
    assert result["rows"] == []
    codes = [item["code"] for item in result["quarantine"]]
    assert codes.count("L_NO_PRICE_VARIATION") == 3


def test_sample_too_small_blocked():
    rows = [_row(PRICES[i], QTYS[i], i + 1) for i in range(3)]
    result = elastic_service.run(rows, "shift")
    assert result["rows"] == []
    codes = [item["code"] for item in result["quarantine"]]
    assert "L_SAMPLE_TOO_SMALL" in codes
    assert "L_NO_PRICE_VARIATION" not in codes


def test_not_significant_marked():
    # 0 销量平移后残差很大，t 值掉到 1.96 以下：结论必须写「不显著」
    rows = [_row(PRICES[i], (0 if i == 1 else QTYS[i]), i + 1) for i in range(len(PRICES))]
    result = elastic_service.run(rows, "shift")
    row = _sku(result)
    assert Decimal(row["tvalue"]) < Decimal("1.96")
    assert row["note"] == "不显著"
    assert row["significant"] == 0
    codes = [item["code"] for item in result["quarantine"]]
    assert "L_NOT_SIGNIFICANT" in codes


def test_real_sample_slopes_match_design():
    # 真实样例：MY-0012 设计弹性 -1.7，MY-0021 设计弹性 -1.3
    settings = config.Settings(db_path=tempfile.mkdtemp() + "/t.sqlite",
                               sample_dir="sample_data")
    store = Store(settings.db_path)
    run = lift_service.run(settings=settings, store=store, dataset="main")
    batch_id = run["batch"]["batch_id"]
    rows = {row["group_key"]: row for row in store.elasticity(batch_id)
            if row["scope"] == "sku"}
    first = Decimal(rows["MY-0012/电商"]["slope"])
    second = Decimal(rows["MY-0021/商超"]["slope"])
    assert Decimal("-1.75") < first < Decimal("-1.45")
    assert Decimal("-1.45") < second < Decimal("-1.15")
    assert int(rows["MY-0012/电商"]["n"]) == 41
    assert int(rows["MY-0021/商超"]["n"]) == 44
    assert all(Decimal(row["r2"]) > 0 for row in rows.values())
    assert all(Decimal(row["se"]) > 0 for row in rows.values())
    assert all(row["note"] == "显著" for row in rows.values())


def test_naive_uncentered_slope_differs():
    # 反例：漏掉中心化、直接 sum(xy)/sum(xx)，斜率正负都会反
    xs = [math.log(float(price)) for price in PRICES]
    ys = [math.log(qty + 0.5) for qty in QTYS]
    naive = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
    fitted = ols.fit(xs, ys)
    assert naive > 0
    assert fitted.slope < 0
    assert abs(naive - fitted.slope) > 1.0
    assert ols.quant(fitted.slope, "0.000001") == BASE["slope"]
    assert ols.quant(naive, "0.000001") == NAIVE_SLOPE


def test_prepare_groups_by_sku_channel_and_pool():
    rows = _rows() + [_row(PRICES[i], QTYS[i], i + 11, sku="MY-0021", channel="商超")
                      for i in range(len(PRICES))]
    samples = {sample.group_key: sample for sample in prepare.build(rows, "shift")}
    assert set(samples) == {"MY-0012/电商", "MY-0021/商超", "电商", "商超", "全渠道"}
    assert samples["MY-0012/电商"].n == BASE["n"]
    assert samples["电商"].n == BASE["n"]
    assert samples["全渠道"].n == 2 * BASE["n"]
    assert samples["全渠道"].price_levels == len(PRICES)
    for sample in samples.values():
        assert prepare.readable(sample) is True
        assert prepare.blocking_code(sample) is None
