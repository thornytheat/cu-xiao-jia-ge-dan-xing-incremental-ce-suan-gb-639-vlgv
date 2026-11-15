"""M7 异方差稳健标准误：HC1 与朴素标准误的比值，用来校正实验期。

朴素标准误假设残差同方差：`Var(b) = s² / Sxx`。促销期几天卖爆、其余几天平稳时，
残差随价格水平放大，同方差假设不成立，标准误被低估，实验期跟着定短。

- 朴素：`s² = Σe² / (n−2)`，`Var_朴素(b) = s² / Sxx`；
- HC0：肉 = `Σ e_i² · (x_i − x̄)²`，`Var_HC0(b) = 肉 / Sxx²`；
- HC1：`Var_HC1 = n/(n−2) · Var_HC0`（小样本修正）；
- 比值 `ratio = SE_HC1 / SE_朴素`，设计阶段用 `max(1, ratio²)` 放大方差。

比值必须≥1 才放大：稳健标准误比朴素小的时候不缩方差，避免把实验期定得比朴素假设还短。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 比值超过这个数就记一条异方差提示，提示门槛与放大动作分开
HETERO_RATIO = 1.25
MIN_DAYS = 3


@dataclass
class RobustFit:
    n: int
    slope: float
    intercept: float
    se_naive: float
    se_hc0: float
    se_hc1: float
    ratio: float
    variance_factor: float
    hetero: bool

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "slope": _q6(self.slope),
            "intercept": _q6(self.intercept),
            "se_naive": _q6(self.se_naive),
            "se_hc0": _q6(self.se_hc0),
            "se_hc1": _q6(self.se_hc1),
            "ratio": _q4(self.ratio),
            "variance_factor": _q4(self.variance_factor),
            "hetero": 1 if self.hetero else 0,
        }


def _q6(value: float) -> str:
    return f"{value:.6f}"


def _q4(value: float) -> str:
    return f"{value:.4f}"


def fit(xs, ys) -> RobustFit | None:
    """对数线性回归 y = a + b·x，同时给出朴素与 HC1 标准误。

    x 与 y 都先减均值再求比，与正规方程等价且数值稳定。
    样本不足、价格无变化或残差全 0 时返回 None，由调用方按朴素处理。
    """
    n = len(xs)
    if n < MIN_DAYS or n != len(ys):
        return None
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    deviations = [x - xbar for x in xs]
    sxx = sum(d * d for d in deviations)
    if sxx <= 0:
        return None
    sxy = sum(deviations[i] * (ys[i] - ybar) for i in range(n))
    slope = sxy / sxx
    intercept = ybar - slope * xbar
    residuals = [ys[i] - (intercept + slope * xs[i]) for i in range(n)]
    ss_res = sum(e * e for e in residuals)
    dof = n - 2
    if dof <= 0:
        return None
    s2 = ss_res / dof
    se_naive = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    meat = sum((residuals[i] ** 2) * (deviations[i] ** 2) for i in range(n))
    se_hc0 = math.sqrt(meat) / sxx
    se_hc1 = se_hc0 * math.sqrt(n / dof)
    ratio = se_hc1 / se_naive if se_naive > 0 else 1.0
    factor = max(1.0, ratio * ratio)
    return RobustFit(n=n, slope=slope, intercept=intercept, se_naive=se_naive,
                     se_hc0=se_hc0, se_hc1=se_hc1, ratio=ratio,
                     variance_factor=factor, hetero=ratio > HETERO_RATIO)


def days(series) -> RobustFit | None:
    """从日序列里取「价格 → 销量」点：同一天的价格取均值，销量取当日合计。

    销量为 0 的天不进对数回归（ln 0 无定义），这些天在缺日与插补规则里单独算。
    """
    xs: list[float] = []
    ys: list[float] = []
    for day in series:
        if not day["present"] or day["qty"] <= 0:
            continue
        price = day["price"]
        if price is None or price <= 0:
            continue
        xs.append(math.log(float(price)))
        ys.append(math.log(float(day["qty"])))
    return fit(xs, ys)


def describe(fit_result: RobustFit | None) -> dict:
    """给页面用的一行摘要；None 时如实写不可估计，不编一个 1.0 出来。"""
    if fit_result is None:
        return {"n": 0, "slope": "—", "se_naive": "—", "se_hc1": "—",
                "ratio": "—", "variance_factor": "1.0000", "hetero": 0,
                "note": "价格或销量没有变化"}
    row = fit_result.as_dict()
    row["note"] = "异方差" if fit_result.hetero else "同方差"
    return row
