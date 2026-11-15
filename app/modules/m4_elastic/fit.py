"""弹性结论：显著性判定、原因码、每组一行可展示结果。

点估计只发表斜率与标准误；「显著 / 不显著」由 |t| 与 1.96 比较得出，
不显著的组仍保留点估计，但结论列写「不显著」，避免区间包含 0 时被当成定论。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config, reasons
from . import ols, prepare


def estimate(samples, zero_mode: str) -> dict:
    rows: list[dict] = []
    quarantine: list[dict] = []
    for sample in samples:
        code = prepare.blocking_code(sample)
        if code is not None:
            quarantine.append({
                "code": code, "stage": "elastic",
                "source": "daily_sales", "line_no": None,
                "field": "含税单价",
                "raw_value": f"n={sample.n} 价格档={sample.price_levels}",
                "detail": f"{sample.scope}:{sample.group_key}",
            })
            continue
        fitted = ols.fit(sample.xs, sample.ys)
        payload = fitted.as_dict()
        significant = abs(Decimal(payload["tvalue"])) >= config.SIGNIFICANCE_T
        note = "显著" if significant else "不显著"
        if not significant:
            quarantine.append({
                "code": "L_NOT_SIGNIFICANT", "stage": "elastic",
                "source": "daily_sales", "line_no": None,
                "field": "含税单价",
                "raw_value": f"t={payload['tvalue']}",
                "detail": f"{sample.scope}:{sample.group_key} 区间包含 0",
            })
        if sample.zero_count:
            zero_code = ("L_ZERO_QTY_DROPPED" if zero_mode == config.ZERO_MODE_DROP
                         else "L_ZERO_QTY_SHIFTED")
            quarantine.append({
                "code": zero_code, "stage": "elastic",
                "source": "daily_sales", "line_no": None, "field": "销量",
                "raw_value": f"0 销量 {sample.zero_count} 条",
                "detail": f"{sample.scope}:{sample.group_key} 处理方式 {zero_mode}",
            })
        rows.append({
            "scope": sample.scope,
            "group_key": sample.group_key,
            "n": payload["n"],
            "zero_count": sample.zero_count,
            "intercept": payload["intercept"],
            "slope": payload["slope"],
            "se": payload["se"],
            "tvalue": payload["tvalue"],
            "r2": payload["r2"],
            "zero_mode": zero_mode,
            "significant": 1 if significant else 0,
            "note": note,
        })
    rows.sort(key=lambda r: (r["scope"], r["group_key"]))
    return {"rows": rows, "quarantine": quarantine}


def pick(rows, scope: str, group_key: str) -> dict | None:
    for row in rows:
        if row["scope"] == scope and row["group_key"] == group_key:
            return row
    return None


def headline(rows) -> dict | None:
    """页面顶部结论用：全渠道合并那一行。"""
    return pick(rows, "all", "全渠道")
