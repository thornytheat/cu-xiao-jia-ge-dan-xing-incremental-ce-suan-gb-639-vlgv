"""整版切换编排：渠道分量分摊 → 三层对账 → 激活/保留旧版。

切换语义（需求硬约束）：
- 三层对账全部平账才把新批次置为 active，同事务把旧 active 置 superseded；
- 任一对账不平：新批次只落 failed 记录，旧版保持 active，页面仍读旧版；
- 同一 batch_key 的重试在 service 入口即幂等复用，既不新增批次也不新增发布行；
  published 表不进批次先删后插清单，切换指针不会被重写清掉。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ... import config, reasons
from . import decompose, reconcile, spread


def allocate_components(promo: decompose.PromoDecomp,
                        channel_gmv: dict[str, Decimal]) -> list[dict]:
    """把一个促销的三个分量分别按渠道实际 GMV 权重做最大余额法分摊。

    每个分量在各渠道分摊合计恒等于分量总额；measured_gmv 是该促销该渠道的
    实测增量（三分量重组的对账基准）。
    """
    weights = {ch: gmv for ch, gmv in channel_gmv.items() if gmv > 0}
    component_amounts = {
        decompose.OWN: promo.own_gmv,
        decompose.CANNIBAL: promo.cannibal_gmv,
        decompose.RESIDUAL: promo.residual_gmv,
    }
    # 实测增量按相同权重摊一次，作为渠道层重组对账的右值基准
    measured_shares = spread.largest_remainder(promo.lift_gmv, weights) \
        if weights else {"全渠道": promo.lift_gmv}
    out: list[dict] = []
    channels = sorted(weights) if weights else ["全渠道"]
    for component, amount in component_amounts.items():
        shares = spread.largest_remainder(amount, weights) \
            if weights else {"全渠道": amount}
        for channel in channels:
            out.append({
                "promo_id": promo.promo_id, "channel": channel,
                "component": component,
                "component_label": decompose.COMPONENT_LABELS[component],
                "lift_gmv": str(shares[channel]),
                "measured_gmv": str(measured_shares[channel]),
            })
    return out


def switch_plan(*, batch_id: str, batch_key: str, dataset: str,
                rule_version: str, decomp: dict, lift_rows: list[dict],
                channel_components: list[dict], previous: dict | None) -> dict:
    """写库前跑三层对账，产出切换计划（不触碰 store）。"""
    report = reconcile.run_all(decomp, lift_rows, channel_components)
    now = dt.datetime.now(config.UTC).isoformat(timespec="seconds")
    status = config.PUBLISH_ACTIVE if report["ok"] else config.PUBLISH_FAILED
    return {
        "status": status,
        "reconcile": report,
        "record": {
            "batch_id": batch_id, "batch_key": batch_key, "dataset": dataset,
            "rule_version": rule_version, "status": status,
            "previous_batch_id": previous["batch_id"] if previous else None,
            "checks_total": len(report["checks"]),
            "checks_failed": len(report["failed"]),
            "published_at": now if report["ok"] else None,
            "note": (reasons.REASONS["A_PUBLISH_ACTIVE"].title if report["ok"]
                     else reasons.REASONS["A_PUBLISH_KEPT_OLD"].title),
        },
        "previous_batch_id": previous["batch_id"] if previous else None,
        "note": "三层对账平账，已整版切换" if report["ok"] else "对账不平，保留旧版",
    }


def commit_switch(store, plan: dict) -> dict:
    """按计划落发布记录：active 同事务顶替旧版，failed 不动旧 active。"""
    store.publish_batch(plan["record"])
    return {"status": plan["status"], "reconcile": plan["reconcile"],
            "previous_batch_id": plan["previous_batch_id"], "note": plan["note"]}


def overlap_quarantine(decomp: dict) -> list[dict]:
    """重叠日与无法归属残差对应的原因码行。"""
    out: list[dict] = []
    seen_dates: set[tuple] = set()
    for row in decomp["overlap_rows"]:
        key = (row["category"], row["channel"], row["sale_date"])
        if key in seen_dates:
            continue
        seen_dates.add(key)
        out.append({
            "code": "A_OVERLAP_CLUSTER", "stage": "lift",
            "source": "promo_calendar.csv", "line_no": None, "field": "促销重叠",
            "raw_value": "/".join(row["promo_ids"]),
            "detail": (f"{row['category_label']}/{row['channel']} {row['sale_date']} "
                       f"{row['promo_count']} 个促销重叠"),
        })
        if Decimal(row["residual_qty"]) != 0:
            out.append({
                "code": "A_RESIDUAL_UNATTRIBUTED", "stage": "lift",
                "source": "daily_sales", "line_no": None, "field": "残差",
                "raw_value": row["residual_qty"],
                "detail": (f"{row['category_label']} 重叠日净缺口 "
                           f"{row['residual_qty']} 件挂品类残差，不摊回商品"),
            })
    for promo in decomp["promos"]:
        if promo.overlap_days and promo.cannibal_qty < 0:
            out.append({
                "code": "A_CANNIBALIZED", "stage": "lift",
                "source": "daily_sales", "line_no": None, "field": "挤占",
                "raw_value": str(promo.cannibal_gmv),
                "detail": (f"{promo.promo_id} 重叠日被挤占 {promo.overlap_days} 天，"
                           "品类内转移净额为 0"),
            })
    return out


__all__ = ["allocate_components", "switch_plan", "commit_switch",
           "overlap_quarantine"]
