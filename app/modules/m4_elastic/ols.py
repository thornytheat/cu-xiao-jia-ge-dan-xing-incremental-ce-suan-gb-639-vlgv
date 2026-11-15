"""对数线性最小二乘：ln q = a + b·ln p，给出斜率、标准误、t 值、R²。

朴素实现两个坑：
1. 直接对 0 销量取 ln → math domain error 整批崩；
2. 用 `sum(x*y)/sum(x*x)` 当斜率（漏掉中心化），价格量级一大斜率就离谱。
本模块对 x、y 都先减均值再求比，等价于正规方程且数值稳定。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass
class Fit:
    n: int
    intercept: float
    slope: float
    se_slope: float
    se_intercept: float
    tvalue: float
    r2: float
    ss_res: float
    ss_tot: float

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "intercept": quant(self.intercept, "0.000001"),
            "slope": quant(self.slope, "0.000001"),
            "se": quant(self.se_slope, "0.000001"),
            "tvalue": quant(self.tvalue, "0.0001"),
            "r2": quant(self.r2, "0.0001"),
        }


def quant(value: float, pattern: str) -> str:
    return str(Decimal(str(value)).quantize(Decimal(pattern), rounding=ROUND_HALF_UP))


def fit(xs, ys) -> Fit:
    n = len(xs)
    if n < 3:
        raise ValueError("样本数少于 3，无法估计斜率")
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx <= 0:
        raise ValueError("价格没有变化，斜率不可识别")
    sxy = sum((xs[i] - xbar) * (ys[i] - ybar) for i in range(n))
    slope = sxy / sxx
    intercept = ybar - slope * xbar
    residuals = [ys[i] - (intercept + slope * xs[i]) for i in range(n)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    dof = n - 2
    s2 = ss_res / dof if dof > 0 else 0.0
    se_slope = math.sqrt(s2 / sxx) if sxx > 0 else 0.0
    se_intercept = math.sqrt(s2 * (1.0 / n + (xbar * xbar) / sxx)) if sxx > 0 else 0.0
    tvalue = slope / se_slope if se_slope > 0 else 0.0
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return Fit(n=n, intercept=intercept, slope=slope, se_slope=se_slope,
               se_intercept=se_intercept, tvalue=tvalue, r2=r2,
               ss_res=ss_res, ss_tot=ss_tot)


def predict(fit_result: Fit, x: float) -> float:
    return fit_result.intercept + fit_result.slope * x
