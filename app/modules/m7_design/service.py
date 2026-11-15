"""M7 编排：读日序列 → 稳健方差 → 三种插补的样本量 → 控制组建议 → 设计结论。

参数一律先校验再算：目标效应、α、功效、预算天数、开始日、缺日补法，任一不合法
都抛 422 并带原因码，不做「取默认值悄悄算一遍」。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ... import config, reasons
from ...errors import Unprocessable
from ..m2_price import coerce
from . import control, impute, power, robust

STAGE = "design"
DEFAULTS = {
    "target_lift": "0.10",
    "alpha": "0.05",
    "power": "0.80",
    "budget_days": "42",
    "impute": "drop",
}
# 历史窗下限：少于这个有效天数就不做设计（不是「凑合算一个」）
MIN_HISTORY_DAYS = 7
MIN_PRESENT_DAYS = 3
MAX_BUDGET_DAYS = 180


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    return str(value).strip()


def _pick(params: dict, key: str) -> str:
    """可选参数：空串、None 一律退回默认值，不把空值当 0 去算。"""
    text = _text(params.get(key))
    return text if text else DEFAULTS[key]


def _ratio(raw) -> float | None:
    """相对效应：`5%` 与 `0.05` 都认；无法解析返回 None。"""
    text = _text(raw)
    if not text:
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    value = coerce.to_decimal(text)
    if value is None:
        return None
    number = float(value) / 100.0 if percent else float(value)
    if number <= 0 or number > power.MAX_RELATIVE_EFFECT:
        return None
    return number


def _float_in(raw, low: float, high: float) -> float | None:
    value = coerce.to_decimal(_text(raw))
    if value is None:
        return None
    number = float(value)
    if not low < number < high:
        return None
    return number


def _budget(raw) -> int | None:
    value = coerce.to_decimal(_text(raw))
    if value is None:
        return None
    number = int(value)
    if number < 1 or number > MAX_BUDGET_DAYS:
        return None
    return number


def _bad(code: str, message: str, detail: dict | None = None):
    return Unprocessable(message, code=code, detail=detail or {})


def series_options(store, batch_id: str) -> list[dict]:
    """可选的目标商品：批次里出现过的「商品 × 渠道」，按商品编码排序。"""
    seen: dict[tuple[str, str], str] = {}
    for row in store.sales(batch_id):
        key = (row["sku"], row["channel"])
        seen.setdefault(key, row.get("sku_name") or "")
    return [{"sku": sku, "channel": channel, "name": seen[(sku, channel)],
             "label": f"{sku} · {channel}"}
            for sku, channel in sorted(seen)]


def default_request(store, batch_id: str | None) -> dict:
    """默认打开设计页时用的参数：优先取已测算促销的商品。"""
    request = dict(DEFAULTS)
    if not batch_id:
        return request
    measured = store.lift(batch_id)
    if measured:
        request["sku"] = measured[0]["sku"]
        request["channel"] = measured[0]["channel"]
        return request
    options = series_options(store, batch_id)
    if options:
        request["sku"] = options[0]["sku"]
        request["channel"] = options[0]["channel"]
    return request


def _resolve_key(store, batch_id: str, params: dict) -> tuple[str, str]:
    options = series_options(store, batch_id)
    if not options:
        raise _bad("D_UNKNOWN_SERIES", "本批次没有可用的日销序列",
                   {"batch_id": batch_id})
    sku = _text(params.get("sku"))
    channel = _text(params.get("channel"))
    if not sku and not channel:
        sku, channel = options[0]["sku"], options[0]["channel"]
    if (sku, channel) not in {(item["sku"], item["channel"]) for item in options}:
        raise _bad("D_UNKNOWN_SERIES",
                   f"本批次没有 {sku}/{channel} 的日销记录",
                   {"sku": sku, "channel": channel,
                    "available": [item["label"] for item in options][:12]})
    return sku, channel


def _start_date(store, batch_id: str, raw) -> dt.date:
    text = _text(raw)
    if text:
        parsed = coerce.to_date(text)
        if parsed is None:
            raise _bad("D_BAD_PARAM", f"开始日无法解析：{text}", {"field": "start_date"})
        return parsed
    dates = [row["sale_date"] for row in store.sales(batch_id)]
    if not dates:
        return dt.date.today()
    return dt.date.fromisoformat(max(dates)) + dt.timedelta(days=1)


def _row(code: str, detail: str, value: str = "") -> dict:
    reason = reasons.get(code)
    return {"code": reason.code, "level": reason.level, "stage": STAGE,
            "title": reason.title, "hint": reason.hint,
            "detail": detail, "raw_value": value}


def _trend_window(series, windows, sku: str, channel: str):
    """前置趋势只在本商品促销开始前的干净窗口里比。

    把促销期也算进趋势，目标的趋势会被自己的促销抬起来，对照组全是偏差。
    干净窗口不足 6 天就退回全窗口，并留一条提示，避免拿半个星期当趋势。
    """
    starts = []
    for window in windows:
        if window["sku"] != sku or window["channel"] != channel:
            continue
        try:
            starts.append(dt.date.fromisoformat(str(window["start_date"])))
        except (TypeError, ValueError):
            continue
    if not starts:
        return None, ""
    first = min(starts)
    low = dt.date.fromisoformat(series[0]["date"])
    days = power.dates_between(low, min(first, dt.date.fromisoformat(series[-1]["date"])))
    if len(days) < control.MIN_HALF_DAYS * 2:
        return None, f"促销开始前的干净窗口只有 {len(days)} 天，趋势按全窗口比"
    return {day.isoformat() for day in days}, ""


def design(store, batch_id: str, params: dict) -> dict:
    """算一次实验设计；返回页面与 API 共用的同一份结构。"""
    sku, channel = _resolve_key(store, batch_id, params)

    target_lift = _ratio(_pick(params, "target_lift"))
    if target_lift is None:
        raise _bad("D_BAD_TARGET",
                   f"目标效应不可用：{_text(params.get('target_lift')) or '空'}",
                   {"field": "target_lift", "accepts": "0.05 或 5%"})
    alpha = _float_in(_pick(params, "alpha"), 0.0, 0.5)
    if alpha is None:
        raise _bad("D_BAD_PARAM", "α 必须落在 0 与 0.5 之间", {"field": "alpha"})
    power_target = _float_in(_pick(params, "power"), 0.5, 1.0)
    if power_target is None:
        raise _bad("D_BAD_PARAM", "功效必须落在 0.5 与 1 之间", {"field": "power"})
    budget_days = _budget(_pick(params, "budget_days"))
    if budget_days is None:
        raise _bad("D_BAD_PARAM", f"预算天数必须是 1 到 {MAX_BUDGET_DAYS} 的整数",
                   {"field": "budget_days"})
    mode = _pick(params, "impute")
    if mode not in impute.MODE_KEYS:
        raise _bad("D_BAD_PARAM", f"缺日补法只能是 {'、'.join(impute.MODE_KEYS)}",
                   {"field": "impute", "value": mode})

    rows = store.sales(batch_id)
    windows = store.promo_windows(batch_id)
    series = power.build_series(rows, sku, channel)
    if series is None:
        raise _bad("D_UNKNOWN_SERIES", f"{sku}/{channel} 没有日销记录",
                   {"sku": sku, "channel": channel})
    present = [day for day in series if day["present"]]
    if len(present) < MIN_PRESENT_DAYS or len(series) < MIN_HISTORY_DAYS:
        raise _bad("D_FLAT_SERIES",
                   f"{sku}/{channel} 只有 {len(present)} 天有效记录，不足以设计",
                   {"sku": sku, "channel": channel, "present": len(present)})

    quarantine: list[dict] = []
    fit_result = robust.days(series)
    if fit_result is None:
        factor = 1.0
        quarantine.append(_row("D_FLAT_PRICE",
                                f"{sku}/{channel} 价格或销量没有变化，按朴素方差处理"))
    else:
        factor = fit_result.variance_factor
        if fit_result.hetero:
            quarantine.append(_row("D_HETERO",
                                   f"稳健标准误是朴素的 {fit_result.ratio:.2f} 倍，"
                                   f"方差已按 {factor:.2f} 放大",
                                   fit_result.as_dict()["ratio"]))

    fit_row = robust.describe(fit_result)
    base = impute.sensitivity(series, target_lift=target_lift, alpha=alpha,
                              power_target=power_target, budget_days=budget_days,
                              baseline_days=config.BASELINE_WINDOW_DAYS,
                              variance_factor=factor)
    chosen = next(row for row in base["rows"] if row["mode"] == mode)
    naive_days = power.required_days(float(chosen["cv"]) / (factor ** 0.5), target_lift,
                                     alpha, power_target, config.BASELINE_WINDOW_DAYS)
    min_days = chosen["min_days"]

    start = _start_date(store, batch_id, params.get("start_date"))
    planned_days = max(budget_days, min_days or budget_days)
    start, window_end = power.window_of(start, planned_days)

    trend_dates, trend_note = _trend_window(series, windows, sku, channel)
    if trend_note:
        quarantine.append(_row("D_SHORT_HISTORY", trend_note))
    candidates = control.build(rows, windows, series, (sku, channel), start, window_end,
                               trend_dates)
    picked = control.best(candidates)
    conflict = control.target_window_conflict(windows, sku, channel, start, window_end)
    if conflict:
        quarantine.append(_row("D_WINDOW_OVERLAP",
                               f"实验期内本商品有 {len(conflict)} 天自有促销，窗口不干净",
                               conflict[0]))
    for item in candidates:
        if item.status == "排除":
            quarantine.append(_row("D_CONTROL_OVERLAP",
                                   f"{item.sku} 在实验期内有 {item.overlap_days} 天自有促销",
                                   item.overlap_dates[0] if item.overlap_dates else ""))
        elif item.status == "偏差":
            quarantine.append(_row("D_CONTROL_TREND",
                                   f"{item.sku} 前置趋势差 {item.trend_gap:.4f}"))
    if picked is None:
        quarantine.append(_row("D_NO_CONTROL", "没有可用的对照候选，基线不可算"))
    else:
        quarantine.append(_row("D_CONTROL_PICKED",
                               f"建议对照 {picked.sku}，前置趋势差 {picked.trend_gap:.4f}",
                               picked.sku))
        if picked.registered:
            quarantine.append(_row("D_CONTROL_REGISTERED",
                                   f"{picked.sku} 与映射表一致"))

    summary = base["summary"]
    quarantine.append(_row(
        "D_IMPUTE_SENSITIVE" if summary["sensitive"] else "D_IMPUTE_STABLE",
        summary["detail"]))

    if min_days is None:
        verdict, verdict_level = "目标效应不可行", "error"
    elif min_days <= budget_days:
        verdict = f"预算够 · 至少 {min_days} 天"
        verdict_level = "ok"
    else:
        verdict = f"预算不足 · {budget_days} 天只能识别 {chosen['mde_pct']}"
        verdict_level = "warn"
        quarantine.append(_row("D_BUDGET_SHORT",
                               f"需要 {min_days} 天，预算 {budget_days} 天",
                               str(min_days)))

    return {
        "batch_id": batch_id,
        "sku": sku,
        "channel": channel,
        "sku_name": next((row["sku_name"] for row in rows
                          if row["sku"] == sku and row["channel"] == channel), ""),
        "target_lift": power.dec(target_lift, "0.0001"),
        "target_lift_pct": power.pct(target_lift, 1),
        "alpha": power.dec(alpha, "0.0001"),
        "power": power.dec(power_target, "0.0001"),
        "budget_days": budget_days,
        "impute": mode,
        "impute_label": impute.MODE_LABELS[mode],
        "start_date": start.isoformat(),
        "end_date": window_end.isoformat(),
        "planned_days": planned_days,
        "baseline_days": config.BASELINE_WINDOW_DAYS,
        "history_days": len(series),
        "history_present": len(present),
        "history_filled": len(series) - len(present),
        "daily_mean": chosen["mean"],
        "daily_sd": chosen["sd"],
        "cv": chosen["cv"],
        "slope": fit_row["slope"],
        "se_naive": fit_row["se_naive"],
        "se_hc1": fit_row["se_hc1"],
        "robust_ratio": fit_row["ratio"],
        "variance_factor": power.dec(factor, "0.0001"),
        "hetero": 1 if (fit_result and fit_result.hetero) else 0,
        "k": power.dec(power.critical_k(alpha, power_target), "0.0001"),
        "min_days": min_days,
        "min_days_text": str(min_days) if min_days is not None else "—",
        "min_days_naive": naive_days,
        "min_days_naive_text": str(naive_days) if naive_days is not None else "—",
        "budget_power": chosen["budget_power"],
        "budget_power_pct": chosen["budget_power_pct"],
        "mde": chosen["mde"],
        "mde_pct": chosen["mde_pct"],
        "interval_low": chosen["interval_low"],
        "interval_high": chosen["interval_high"],
        "contains_zero": chosen["contains_zero"],
        "verdict": verdict,
        "verdict_level": verdict_level,
        "control_pick": picked.as_dict() if picked else None,
        "control_candidates": [item.as_dict() for item in candidates],
        "impute_rows": base["rows"],
        "impute_summary": summary,
        "window_conflict": conflict,
        "quarantine": quarantine,
    }
