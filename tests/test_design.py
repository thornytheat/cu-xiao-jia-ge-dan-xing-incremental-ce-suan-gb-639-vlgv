"""M7 实验设计：样本量、功效、稳健标准误、控制组选择、缺日插补敏感性。

样例期望值来自 `tests/fixtures/design_cases.json`（手算回填），测试不重算公式。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
from decimal import Decimal

from app import config
from app.modules.m7_design import control, impute, power, robust
from app.modules.m7_design import service as design_service

CASES = json.loads(
    (pathlib.Path(__file__).resolve().parent / "fixtures" / "design_cases.json")
    .read_text(encoding="utf-8"))
START = dt.date.fromisoformat("2026-09-01")


def _series(values) -> list[dict]:
    out = []
    for index, value in enumerate(values):
        day = (START + dt.timedelta(days=index)).isoformat()
        out.append({"date": day, "present": value is not None,
                    "gmv": None if value is None else Decimal(str(value)),
                    "qty": 0, "price": None})
    return out


def _rows(sku: str, channel: str, values) -> list[dict]:
    out = []
    for index, value in enumerate(values):
        if value is None:
            continue
        out.append({"sku": sku, "channel": channel,
                    "sale_date": (START + dt.timedelta(days=index)).isoformat(),
                    "amount_excl": str(value), "qty": int(value),
                    "price_excl": "12.8000"})
    return out


def _window(sku: str, channel: str, start: str, end: str,
            promo_id: str = "P1") -> dict:
    return {"promo_id": promo_id, "sku": sku, "channel": channel,
            "start_date": start, "end_date": end}


def test_z_quantile_matches_standard_table():
    # 分位数是样本量公式的输入端，错一位实验期就差几天
    ref = CASES["power"]
    assert f"{power.z_quantile(0.975):.6f}" == ref["z_975"]
    assert f"{power.z_quantile(0.80):.6f}" == ref["z_080"]
    assert f"{power.critical_k(0.05, 0.80):.4f}" == ref["k_alpha_005_power_080"]
    assert power.normal_cdf(0.0) == 0.5
    assert round(power.normal_cdf(1.959963986120195), 6) == 0.975


def test_required_days_matches_closed_form():
    ref = CASES["power"]
    cv = ref["cv"]
    assert power.required_days(cv, 0.05, 0.05, 0.80, ref["baseline_days"]) == \
        ref["days_target_005"]
    assert power.required_days(cv, 0.10, 0.05, 0.80, ref["baseline_days"]) == \
        ref["days_target_010"]
    # 目标效应越小、波动越大，所需天数单调变长
    assert power.required_days(cv * 2, 0.10, 0.05, 0.80, ref["baseline_days"]) > \
        ref["days_target_010"]


def test_power_reaches_target_at_minimum_days():
    ref = CASES["power"]
    days = power.required_days(ref["cv"], 0.05, 0.05, 0.80, ref["baseline_days"])
    hit = power.power_at(days, ref["cv"], 0.05, 0.05, ref["baseline_days"])
    assert 0.79 < hit < 0.83
    assert power.power_at(days - 3, ref["cv"], 0.05, 0.05, ref["baseline_days"]) < hit


def test_detectable_effect_matches_budget_power():
    ref = CASES["power"]
    mde = power.detectable_effect(42, 0.1591, 0.05, 0.80, ref["baseline_days"])
    assert f"{mde:.4f}" == ref["mde_at_42"]
    # 可识别效应是门槛：按它当目标效应，功效刚好落到目标值
    assert abs(power.power_at(42, 0.1591, mde, 0.05, ref["baseline_days"]) - 0.80) < 0.01
    # 天数越多，能识别的效应越小
    assert power.detectable_effect(60, 0.1591, 0.05, 0.80, 14) < mde


def test_interval_contains_zero_when_power_is_low():
    # 不变量：预算下功效低于一半时，预期区间一定含 0；功效高时不含 0
    wide = power.interval(1039.52, 319.97, 30, 0.08, 0.05, 14)
    assert wide["contains_zero"] is True
    assert power.power_at(30, 0.3394, 0.08, 0.05, 14) < 0.5
    tight = power.interval(1039.52, 64.77, 30, 0.08, 0.05, 14)
    assert tight["contains_zero"] is False
    assert power.power_at(30, 0.0623, 0.08, 0.05, 14) > 0.5
    assert Decimal(wide["low"]) < Decimal(tight["low"])


def test_hc1_ratio_and_variance_factor_on_heteroskedastic_series():
    case = CASES["hetero"]
    fit = robust.fit([math.log(price) for price in case["prices"]],
                     [math.log(qty) for qty in case["qtys"]])
    expected = case["expected"]
    payload = fit.as_dict()
    assert payload["n"] == expected["n"]
    assert payload["slope"] == expected["slope"]
    assert payload["se_naive"] == expected["se_naive"]
    assert payload["se_hc1"] == expected["se_hc1"]
    assert payload["ratio"] == expected["ratio"]
    assert payload["variance_factor"] == expected["variance_factor"]
    assert payload["hetero"] == expected["hetero"]
    # 放大方差后所需天数一定变长：朴素 14 天，稳健 20 天
    naive = power.required_days(expected["cv"], expected["target_lift"], 0.05, 0.80, 14)
    robust_days = power.required_days(expected["cv"] * (fit.variance_factor ** 0.5),
                                      expected["target_lift"], 0.05, 0.80, 14)
    assert naive == expected["days_naive"]
    assert robust_days == expected["days_robust"]
    assert robust_days > naive


def test_hc1_has_no_fit_without_price_variation():
    flat = [{"date": "2026-09-01", "present": True, "gmv": Decimal("1000"),
             "qty": 100, "price": Decimal("12.8000")},
            {"date": "2026-09-02", "present": True, "gmv": Decimal("1010"),
             "qty": 101, "price": Decimal("12.8000")},
            {"date": "2026-09-03", "present": True, "gmv": Decimal("990"),
             "qty": 99, "price": Decimal("12.8000")}]
    assert robust.days(flat) is None
    described = robust.describe(None)
    assert described["ratio"] == "—"
    assert described["variance_factor"] == "1.0000"
    assert "没有变化" in described["note"]


def test_control_excludes_candidate_with_promo_in_experiment_window():
    # TRAP-28：对照商品在实验期内自己促销，同期倍数把它自己的促销算进基线
    target = _series([1000] * 20)
    rows = (_rows("MY-0012", "电商", [1000] * 20)
            + _rows("MY-0019", "电商", [1000] * 20)
            + _rows("MY-0021", "电商", [1000] * 20))
    start, end = power.window_of(dt.date.fromisoformat("2026-09-21"), 14)
    windows = [_window("MY-0021", "电商", "2026-09-25", "2026-09-30")]
    candidates = control.build(rows, windows, target, ("MY-0012", "电商"), start, end)
    by_sku = {item.sku: item for item in candidates}
    assert by_sku["MY-0021"].status == "排除"
    assert by_sku["MY-0021"].code == "D_CONTROL_OVERLAP"
    assert by_sku["MY-0021"].overlap_days == 5
    assert by_sku["MY-0019"].status == "可用"
    assert control.best(candidates).sku == "MY-0019"
    # 左闭右开：对照促销结束日正好是实验开始日，不算重叠
    edge = [_window("MY-0021", "电商", "2026-09-10", "2026-09-21")]
    again = control.build(rows, edge, target, ("MY-0012", "电商"), start, end)
    assert {item.sku: item.overlap_days for item in again}["MY-0021"] == 0


def test_control_rank_puts_registered_first_then_gap():
    target = _series([1000] * 20)
    rows = (_rows("MY-0012", "电商", [1000] * 20)
            + _rows("MY-0019", "电商", [1000] * 10 + [1051] * 10)
            + _rows("MY-0034", "电商", [1000] * 20)
            + _rows("MY-0028", "电商", [1000] * 10 + [1250] * 10))
    start, end = power.window_of(dt.date.fromisoformat("2026-09-21"), 14)
    candidates = control.build(rows, [], target, ("MY-0012", "电商"), start, end)
    by_sku = {item.sku: item for item in candidates}
    # 趋势差最小的不是映射里那个，但映射优先：MY-0019 排第一
    assert Decimal(by_sku["MY-0034"].trend_gap) < Decimal(by_sku["MY-0019"].trend_gap)
    assert by_sku["MY-0019"].registered is True
    assert [item.sku for item in candidates][0] == "MY-0019"
    assert control.best(candidates).sku == "MY-0019"
    # 趋势差超过 10% 的候选标为偏差，不进建议
    assert by_sku["MY-0028"].status == "偏差"
    assert by_sku["MY-0028"].code == "D_CONTROL_TREND"


def test_impute_sensitivity_flips_on_light_missing():
    # TRAP-27：2 天缺日按 0 补，波动被放大，区间从「不含 0」变成「含 0」
    case = CASES["impute"]["light"]
    result = impute.sensitivity(_series(case["values"]),
                                target_lift=float(case["target_lift"]),
                                alpha=float(case["alpha"]),
                                power_target=float(case["power"]),
                                budget_days=case["budget_days"],
                                baseline_days=case["baseline_days"],
                                variance_factor=1.0)
    expected = case["expected"]
    rows = {row["mode"]: row for row in result["rows"]}
    assert result["missing_days"] == expected["missing_days"]
    for mode in ("drop", "zero", "mean"):
        assert rows[mode]["min_days"] == expected["min_days"][mode]
        assert rows[mode]["contains_zero"] is expected["contains_zero"][mode]
        assert rows[mode]["interval_low"] == expected["interval_low"][mode]
        assert rows[mode]["budget_power"] == expected["budget_power"][mode]
    assert result["summary"]["significance_flip"] is expected["significance_flip"]
    assert result["summary"]["size_flip"] is expected["size_flip"]
    assert result["summary"]["sensitive"] is expected["sensitive"]
    assert rows["zero"]["verdict"] == "预算不足"
    assert rows["drop"]["verdict"] == "预算够"


def test_impute_three_modes_identical_when_no_missing():
    case = CASES["impute"]["complete"]
    result = impute.sensitivity(_series(case["values"]),
                                target_lift=float(case["target_lift"]),
                                alpha=float(case["alpha"]),
                                power_target=float(case["power"]),
                                budget_days=case["budget_days"],
                                baseline_days=case["baseline_days"],
                                variance_factor=1.0)
    expected = case["expected"]
    assert result["missing_days"] == expected["missing_days"]
    assert result["summary"]["sensitive"] is expected["sensitive"]
    assert result["summary"]["detail"] == expected["detail"]
    assert {row["min_days"] for row in result["rows"]} == {expected["min_days"]["drop"]}
    # 没有缺日时三种补法的日均与波动必须逐位相同
    assert len({row["mean"] for row in result["rows"]}) == 1
    assert len({row["sd"] for row in result["rows"]}) == 1


def test_impute_sparse_series_has_no_solution_under_zero_fill():
    case = CASES["impute"]["sparse"]
    result = impute.sensitivity(_series(case["values"]),
                                target_lift=float(case["target_lift"]),
                                alpha=float(case["alpha"]),
                                power_target=float(case["power"]),
                                budget_days=case["budget_days"],
                                baseline_days=case["baseline_days"],
                                variance_factor=1.0)
    expected = case["expected"]
    rows = {row["mode"]: row for row in result["rows"]}
    assert rows["zero"]["min_days"] is expected["min_days"]["zero"]
    assert rows["zero"]["min_days_text"] == "—"
    assert rows["zero"]["verdict"] == "不可行"
    assert rows["mean"]["min_days"] == expected["min_days"]["mean"]
    assert result["summary"]["sensitive"] is True


def test_design_on_sample_data_picks_registered_control(settings, store, batch_id):
    params = design_service.default_request(store, batch_id)
    plan = design_service.design(store, batch_id, params)
    assert (plan["sku"], plan["channel"]) == ("MY-0012", "电商")
    assert plan["target_lift_pct"] == "10.0%"
    assert plan["budget_days"] == 42
    assert plan["min_days"] == 30
    assert plan["min_days_naive"] == 30
    assert plan["budget_power_pct"] == "94.1%"
    assert plan["mde_pct"] == "7.9%"
    assert plan["contains_zero"] is False
    assert plan["verdict"] == "预算够 · 至少 30 天"
    assert plan["verdict_level"] == "ok"
    assert plan["control_pick"]["sku"] == "MY-0019"
    assert plan["control_pick"]["registered"] == 1
    # 真实样例里缺日按 0 补会让区间含 0，敏感性必须标出来
    assert plan["impute_summary"]["significance_flip"] is True
    codes = {row["code"] for row in plan["quarantine"]}
    assert {"D_CONTROL_PICKED", "D_CONTROL_REGISTERED", "D_IMPUTE_SENSITIVE"} <= codes
    assert config.BASELINE_WINDOW_DAYS == plan["baseline_days"]


def test_design_window_is_half_open(settings, store, batch_id):
    plan = design_service.design(store, batch_id,
                                 design_service.default_request(store, batch_id))
    start = dt.date.fromisoformat(plan["start_date"])
    end = dt.date.fromisoformat(plan["end_date"])
    assert start == dt.date.fromisoformat("2026-11-01")
    assert (end - start).days == plan["planned_days"] == 42
    assert power.window_of(start, plan["planned_days"]) == (start, end)
    assert power.dates_between(start, end)[-1] == dt.date.fromisoformat("2026-12-12")
