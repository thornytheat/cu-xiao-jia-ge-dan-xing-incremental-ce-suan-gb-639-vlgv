"""弹性编排：准备样本 → 逐组拟合 → 汇总可展示结论。"""
from __future__ import annotations

from ... import config
from . import fit, prepare


def run(rows, zero_mode: str | None = None) -> dict:
    mode = zero_mode or config.ZERO_MODE_SHIFT
    samples = prepare.build(rows, mode)
    result = fit.estimate(samples, mode)
    return {
        "rows": result["rows"],
        "quarantine": result["quarantine"],
        "headline": fit.headline(result["rows"]),
        "zero_mode": mode,
        "samples": [
            {"scope": s.scope, "group_key": s.group_key, "n": s.n,
             "zero_count": s.zero_count, "price_levels": s.price_levels,
             "readable": prepare.readable(s)}
            for s in samples
        ],
    }
