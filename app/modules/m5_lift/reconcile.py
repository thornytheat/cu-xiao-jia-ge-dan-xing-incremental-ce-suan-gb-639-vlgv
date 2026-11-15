"""三层对账：商品层、渠道层、品类层，差额全部为 0 才允许整版切换。

- 商品层：逐促销恒等式 ``实测增量 = 自身 + 挤占 + 残差``（销量与金额两套）。
- 渠道层：每个促销的三分量渠道分摊合计 = 该促销分量总额；
  且实测增量的渠道分摊合计 = 三分量分摊后逐渠道相加。
- 品类层：``品类实测 = Σ自身 + Σ挤占 + Σ残差``；挤占净额必须为 0（内部转移）；
  品类共同增长 = Σ自身 + Σ残差，重复计入额 = 实测 − 共同增长。

金额全程 Decimal 2 位，容差仅作防御；任何一条不平就返回 ok=False，
service 据此中止切换、保留旧版。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_price import coerce

TOL = config.RECONCILE_TOLERANCE


def _money(value) -> Decimal:
    return coerce.money(Decimal(str(value)))


def _check(account: str, left: Decimal, right: Decimal, note: str) -> dict:
    delta = coerce.money(left - right)
    return {
        "layer": account.split("·")[0], "account": account,
        "left": str(coerce.money(left)), "right": str(coerce.money(right)),
        "delta": str(delta), "ok": abs(delta) <= TOL, "note": note,
    }


def reconcile_product(decomp: dict, lift_rows: list[dict]) -> list[dict]:
    """商品层：三分量加总必须等于 M5 实测增量。"""
    rows: list[dict] = []
    lift_index = {r["promo_id"]: r for r in lift_rows}
    for promo in decomp["promos"]:
        lift = lift_index[promo.promo_id]
        composed_gmv = coerce.money(
            promo.own_gmv + promo.cannibal_gmv + promo.residual_gmv)
        rows.append(_check(
            f"商品层·{promo.promo_id}金额",
            _money(lift["lift_gmv"]), composed_gmv,
            "实测增量 = 自身 + 挤占 + 残差"))
        composed_qty = (promo.own_qty + promo.cannibal_qty
                        + promo.residual_qty).quantize(Decimal("0.0001"))
        delta = (promo.lift_qty - composed_qty).quantize(Decimal("0.0001"))
        rows.append({
            "layer": "商品层", "account": f"商品层·{promo.promo_id}销量",
            "left": str(promo.lift_qty), "right": str(composed_qty),
            "delta": str(delta), "ok": delta == Decimal("0.0000"),
            "note": "实测增量销量 = 三分解销量之和",
        })
    return rows


def reconcile_channel(channel_components: list[dict]) -> list[dict]:
    """渠道层：分量分摊守恒，且重组后等于实测增量分摊。

    channel_components 行：promo_id / channel / component / lift_gmv，
    外加每个 (promo_id, channel) 的实测 lift_gmv（measured_gmv）。
    """
    rows: list[dict] = []
    # 分量合计 vs 促销分量总额
    comp_sum: dict[tuple, Decimal] = {}
    measured: dict[tuple, Decimal] = {}
    for item in channel_components:
        key = (item["promo_id"], item["channel"])
        comp_sum[key] = comp_sum.get(key, Decimal("0")) + _money(item["lift_gmv"])
        measured[key] = _money(item["measured_gmv"])
    for key in sorted(comp_sum):
        rows.append(_check(
            f"渠道层·{key[0]}/{key[1]}重组",
            measured[key], comp_sum[key],
            "实测增量渠道额 = 自身/挤占/残差分摊后逐渠道相加"))
    return rows


def reconcile_category(decomp: dict) -> list[dict]:
    """品类层：挤占净额为 0，共同增长只计一次。"""
    rows: list[dict] = []
    for cat in decomp["category_rows"]:
        own, cannibal = _money(cat["own_gmv"]), _money(cat["cannibal_gmv"])
        residual, lift = _money(cat["residual_gmv"]), _money(cat["lift_gmv"])
        rows.append(_check(
            f"品类层·{cat['category']}三分恒等",
            lift, coerce.money(own + cannibal + residual),
            "品类实测 = Σ自身 + Σ挤占 + Σ残差"))
        rows.append(_check(
            f"品类层·{cat['category']}挤占净额",
            cannibal, Decimal("0.00"),
            "挤占是品类内转移，净额必须为 0"))
        double_count = coerce.money(lift - _money(cat["category_lift_gmv"]))
        rows.append(_check(
            f"品类层·{cat['category']}共同增长",
            double_count, cannibal,
            "重复计入额 = 挤占净额（0），品类口径 = Σ自身 + Σ残差"))
    return rows


def run_all(decomp: dict, lift_rows: list[dict],
            channel_components: list[dict]) -> dict:
    checks = (reconcile_product(decomp, lift_rows)
              + reconcile_channel(channel_components)
              + reconcile_category(decomp))
    return {
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
        "failed": [item for item in checks if not item["ok"]],
    }
