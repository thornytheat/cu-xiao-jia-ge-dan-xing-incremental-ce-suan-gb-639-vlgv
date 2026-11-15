"""M2 归价：千分位/全角、哨兵、包装折算、含税折不含税、区间与版本优先级。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import config
from app.modules.m2_price import coerce, contract, versions
from fixtures import dataset as fx


def test_thousands_and_fullwidth_price():
    # TRAP-06：float("1,234.50") / float("１２.８０") 都抛 ValueError
    value, notes = coerce.to_price_per_piece("1,234.50", "元/件")
    assert value == Decimal("1234.5000")
    assert notes == []
    wide, _ = coerce.to_price_per_piece("１２.８０", "元/件")
    assert wide == Decimal("12.8000")
    full, _ = coerce.to_price_per_piece("１２，８０", "元/件")
    assert full == Decimal("1280.0000")


def test_sentinel_price_is_three_state():
    # TRAP-07："-" / "N/A" / "未知" 不是 0，不能参与合计
    for raw in ("-", "N/A", "未知", "", "  — "):
        assert coerce.to_decimal(raw) is None
        assert coerce.is_sentinel(raw) is True
    assert coerce.to_decimal("0") == Decimal("0")
    assert coerce.is_sentinel("0") is False
    assert coerce.to_qty("-") is None
    assert coerce.to_qty("0") == 0


def test_pack_unit_converted_to_piece():
    # TRAP-15：元/箱 直接当元/件用，合计差 12 倍
    value, notes = coerce.to_price_per_piece("1,234.50", "元/箱")
    assert value == Decimal("102.8750")
    assert notes == ["P_UNIT_CONVERTED"]
    inline, notes2 = coerce.to_price_per_piece("153.60 元/箱", "元/件")
    assert inline == Decimal("12.8000")
    assert notes2 == ["P_UNIT_CONVERTED"]
    same, none = coerce.to_price_per_piece("12.80", "元/件")
    assert same == Decimal("12.8000")
    assert none == []


def test_tax_conversion_keeps_four_decimals():
    # TRAP-10：用 float 折算再累加，1000 行会差 0.01
    price_incl, _ = coerce.to_price_per_piece("8.90", "元/件")
    rate, note = coerce.to_tax_rate("0.13")
    assert note is None
    excl = coerce.excl_of(price_incl, rate)
    assert excl == Decimal("7.8761")
    total = sum((excl * Decimal(qty) for qty in (13, 17, 19, 23)), Decimal("0"))
    assert coerce.money(total) == Decimal("567.08")
    fallback, bad_note = coerce.to_tax_rate("0.06")
    assert fallback == config.DEFAULT_TAX
    assert bad_note == "P_TAX_UNKNOWN"


def test_window_is_half_open_at_end():
    # TRAP-23：闭区间会让 v2 结束日与 v3 开始日各算一天
    _, records = fx.as_records(fx.PROMO_HEADER, [
        fx.promo("P1", "MY-0012", "电商", 1, "2026-10-01", "2026-10-03", "9.00"),
        fx.promo("P1", "MY-0012", "电商", 2, "2026-10-03", "2026-10-05", "8.00"),
    ])
    built = versions.build(records)
    windows = {(w.version, w.start.isoformat(), w.end.isoformat()) for w in built["window_objs"]}
    assert windows == {(1, "2026-10-01", "2026-10-03"), (2, "2026-10-03", "2026-10-05")}
    assert built["overlap_days"]["P1"] == 0
    assert sum(w.days for w in built["window_objs"]) == 4


def test_version_overlap_marked_and_highest_wins():
    # TRAP-25：两版同时生效，取到哪版看运气
    _, records = fx.as_records(fx.PROMO_HEADER, [
        fx.promo("P1", "MY-0012", "电商", 1, "2026-10-01", "2026-10-06", "9.00"),
        fx.promo("P1", "MY-0012", "电商", 2, "2026-10-04", "2026-10-09", "8.00"),
    ])
    built = versions.build(records)
    codes = [row["code"] for row in built["quarantine"]]
    assert "Q_PROMO_OVERLAP" in codes
    assert built["overlap_days"]["P1"] == 2
    spans = [(w.version, w.start.isoformat(), w.end.isoformat())
             for w in built["window_objs"]]
    assert spans == [(1, "2026-10-01", "2026-10-04"), (2, "2026-10-04", "2026-10-09")]


def test_rule_order_independence_on_versions():
    # TRAP-19：结果不能取决于文件书写顺序
    forward = [
        fx.promo("P1", "MY-0012", "电商", 1, "2026-10-01", "2026-10-06", "9.00"),
        fx.promo("P1", "MY-0012", "电商", 2, "2026-10-04", "2026-10-09", "8.00"),
    ]
    _, first = fx.as_records(fx.PROMO_HEADER, forward)
    _, second = fx.as_records(fx.PROMO_HEADER, list(reversed(forward)))
    a = [(w.version, w.start.isoformat(), str(w.price_excl))
         for w in versions.build(first)["window_objs"]]
    b = [(w.version, w.start.isoformat(), str(w.price_excl))
         for w in versions.build(second)["window_objs"]]
    assert a == b


def test_inactive_versions_excluded_with_reason():
    _, records = fx.as_records(fx.PROMO_HEADER, [
        fx.promo("P9", "MY-0045", "商超", 1, "2026-10-01", "2026-10-05", "21.00", "待审"),
        fx.promo("P8", "MY-0034", "电商", 1, "2026-10-01", "2026-10-05", "0.00", "生效"),
        fx.promo("P7", "MY-0034", "电商", 1, "2026-10-05", "2026-10-01", "12.00", "生效"),
    ])
    built = versions.build(records)
    assert built["window_objs"] == []
    assert built["pending_promos"] == ["P9"]
    assert built["blocked_promos"] == ["P7", "P8"]
    codes = sorted(row["code"] for row in built["quarantine"])
    assert codes == ["P_PRICE_ZERO", "Q_PROMO_BAD_RANGE", "Q_PROMO_NO_VERSION"]


def test_naive_float_parse_would_raise():
    # 反例：朴素实现直接 float(单元格)，带千分位或单位后缀的都会抛
    with pytest.raises(ValueError):
        float("1,234.50 元/箱")
    with pytest.raises(ValueError):
        float("12.80 元/件")
    with pytest.raises(ValueError):
        float("1，234.50")
    assert coerce.to_price_per_piece("1,234.50 元/箱", "")[0] == Decimal("102.8750")


def test_contract_exposes_units_and_rules():
    payload = contract.contract()
    assert payload["price_unit"] == "元/件"
    assert payload["decimals"]["money"] == 2
    assert payload["decimals"]["price"] == 4
    keys = [field["name"] for field in payload["fields"]]
    assert keys[:3] == ["日期", "渠道", "商品编码"]
    rule_keys = {rule["key"] for rule in payload["rules"]}
    assert {"price_chain", "pack", "window", "version", "zero_qty", "baseline"} <= rule_keys
    assert payload["significance_t"] == "1.96"


def test_zero_length_promo_range_rejected():
    # 边界：结束日等于开始日，左闭右开区间长度为 0，必须拒绝而不是算成一天
    _, records = fx.as_records(fx.PROMO_HEADER, [
        fx.promo("P6", "MY-0012", "电商", 1, "2026-10-05", "2026-10-05", "9.50"),
    ])
    built = versions.build(records)
    assert built["window_objs"] == []
    assert built["blocked_promos"] == ["P6"]
    assert [row["code"] for row in built["quarantine"]] == ["Q_PROMO_BAD_RANGE"]
    assert built["quarantine"][0]["raw_value"] == "2026-10-05~2026-10-05"
