"""同品类促销重叠的增量三分：自身增量、挤占、无法归属残差。

背景：同品类多个商品同期促销时，每个商品按自身弹性算出的价格解释量都把
重叠客流当成了自己的增量，品类汇总会把同一批客流重复计入。

分解只用三个可解释信号，不引入交叉价格弹性，也不用固定比例把差额摊回商品：

1. 价格变化 → 自身增量（own）
   自身弹性 b 与实际价格变化 Δln p 给出的反事实销量
   ``q_own = 基线销量 × (exp(b·Δln p) − 1)``。基线销量已经乘过对照组同期倍数，
   对照走势在基线里扣掉，不再进任何增量分量。
2. 促销重叠 → 挤占（cannibal）
   只在「同品类、同渠道、≥2 个生效促销」的重叠日识别。重叠日各促销的未解释
   偏差 d = 实测增量 − 自身增量：赢家的正偏差与输家的负偏差按观测缺口逐份匹配，
   匹配上的部分是品类内转移（挤占），品类净额恒为 0。匹配比例来自当天实际
   观测到的缺口，不是预设的固定系数。
3. 对照走势之外仍解释不了的部分 → 残差（residual）
   非重叠日的偏差直接归属该商品（来源清晰）；重叠日匹配不掉的净缺口无法在
   商品间归属，挂在品类层，**绝不摊回商品**。

恒等式（金额经 Decimal 2 位量化，舍入差并入残差）：
    实测增量 = 自身增量 + 挤占增量 + 残差增量
    品类增量 = Σ自身增量 + Σ残差增量（Σ挤占 ≡ 0，共同增长只计一次）
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from decimal import Decimal

from ... import config
from ..m2_price import coerce

Q4 = Decimal("0.0001")
# 分量键
OWN = "own"
CANNIBAL = "cannibal"
RESIDUAL = "residual"
COMPONENTS = (OWN, CANNIBAL, RESIDUAL)
COMPONENT_LABELS = {
    OWN: "自身增量",
    CANNIBAL: "挤占",
    RESIDUAL: "无法归属残差",
}


@dataclass
class DayTerms:
    """单个促销某一天的分解中间量（销量口径，4 位小数）。"""

    sale_date: dt.date
    is_overlap: bool
    base_qty: Decimal          # 对照走势调整后的基线销量
    actual_qty: Decimal        # 实测销量（缺日为 0，天数账已在 M3 记过）
    own_qty: Decimal           # 价格变化解释的反事实增量
    deviation_qty: Decimal     # 未解释偏差 = 实测增量 − 自身增量
    price_excl: Decimal        # 当天生效不含税价，用于金额量化
    cannibal_qty: Decimal = Decimal("0.0000")
    residual_qty: Decimal = Decimal("0.0000")


@dataclass
class PromoDecomp:
    promo_id: str
    sku: str
    channel: str
    category: str
    days_total: int = 0
    overlap_days: int = 0
    # 销量分量（4 位）
    own_qty: Decimal = Decimal("0.0000")
    cannibal_qty: Decimal = Decimal("0.0000")
    residual_qty: Decimal = Decimal("0.0000")
    lift_qty: Decimal = Decimal("0.0000")
    # 金额分量（2 位），金额恒等式由 _money_split 保证
    own_gmv: Decimal = Decimal("0.00")
    cannibal_gmv: Decimal = Decimal("0.00")
    residual_gmv: Decimal = Decimal("0.00")
    lift_gmv: Decimal = Decimal("0.00")
    day_terms: list[DayTerms] = field(default_factory=list)


def category_of(sku: str) -> str:
    return config.CATEGORY_MAP.get(sku, f"CAT-UNMAPPED:{sku}")


def _daily_index(sales) -> dict[tuple, list]:
    """按 (sku, channel, date) 聚合当日行（去重后一天至多一条，仍做累加防御）。"""
    index: dict[tuple, list] = {}
    for row in sales:
        index.setdefault((row.sku, row.channel, row.sale_date), []).append(row)
    return index


def _overlap_dates(promos: list[PromoDecomp], window_index) -> dict[str, set]:
    """返回每个促销的重叠日集合：同品类同渠道同日有 ≥2 个生效促销。"""
    # (category, channel, date) -> [promo_id,...]
    bucket: dict[tuple, list[str]] = {}
    for promo in promos:
        for window in window_index.get(promo.promo_id, []):
            cursor = window.start
            while cursor < window.end:
                bucket.setdefault((promo.category, promo.channel, cursor), []) \
                    .append(promo.promo_id)
                cursor += dt.timedelta(days=1)
    out: dict[str, set] = {p.promo_id: set() for p in promos}
    for (_cat, _ch, day), promo_ids in bucket.items():
        if len(set(promo_ids)) >= 2:
            for promo_id in promo_ids:
                out[promo_id].add(day)
    return out


def _day_price(promo_id: str, day: dt.date, window_index) -> Decimal:
    for window in window_index.get(promo_id, []):
        if window.start <= day < window.end:
            return window.price_excl
    return Decimal("0")


def _money_split(amount_terms: dict[str, Decimal],
                 total_gmv: Decimal) -> dict[str, Decimal]:
    """三个分量金额各自量化到 2 位，舍入差并入残差，保证金额恒等式。"""
    raw = {key: coerce.money(value) for key, value in amount_terms.items()}
    gap = coerce.money(total_gmv - sum(raw.values(), Decimal("0")))
    raw[RESIDUAL] = coerce.money(raw[RESIDUAL] + gap)
    return raw


def build(sales, windows, lift_rows, elastic_rows) -> dict:
    """计算三分分解。

    返回 ``{"promos": [PromoDecomp...], "overlap_rows": [...], "category_rows": [...]}``。
    只处理已通过 M5 测算、出现在 lift_rows 里的促销；没有弹性斜率的促销已在 M5 阻断。
    """
    window_index: dict[str, list] = {}
    for window in windows:
        window_index.setdefault(window.promo_id, []).append(window)

    slope_index = {
        (r["group_key"]): Decimal(str(r["slope"])) for r in elastic_rows
        if r["scope"] == "sku"
    }
    lift_index = {r["promo_id"]: r for r in lift_rows}
    daily = _daily_index(sales)

    promos: list[PromoDecomp] = []
    for row in lift_rows:
        promos.append(PromoDecomp(
            promo_id=row["promo_id"], sku=row["sku"], channel=row["channel"],
            category=category_of(row["sku"]),
        ))
    if not promos:
        return {"promos": [], "overlap_rows": [], "category_rows": []}

    overlap_sets = _overlap_dates(promos, window_index)

    # ---- 第一步：逐日算基线、实测、自身价格解释量与未解释偏差 ----
    for promo in promos:
        lift_row = lift_index[promo.promo_id]
        slope = slope_index.get(f"{promo.sku}/{promo.channel}", Decimal("0"))
        dlnp = float(lift_row["dlnp"]) if lift_row.get("dlnp") not in (None, "", "0") else 0.0
        own_factor = Decimal(str(_exp_factor(slope, dlnp)))  # exp(b·Δlnp)
        dates = sorted(d for w in window_index[promo.promo_id]
                       for d in _daterange(w.start, w.end))
        base_daily = Decimal(lift_row["base_qty"]) / Decimal(len(dates))
        base_daily = base_daily.quantize(Q4)
        for day in dates:
            rows = daily.get((promo.sku, promo.channel, day), [])
            actual = sum((Decimal(r.qty) for r in rows), Decimal("0"))
            own_qty = (base_daily * (own_factor - Decimal("1"))).quantize(Q4)
            measured = (actual - base_daily).quantize(Q4)
            deviation = (measured - own_qty).quantize(Q4)
            promo.day_terms.append(DayTerms(
                sale_date=day,
                is_overlap=day in overlap_sets[promo.promo_id],
                base_qty=base_daily, actual_qty=actual,
                own_qty=own_qty, deviation_qty=deviation,
                price_excl=_day_price(promo.promo_id, day, window_index),
            ))

    # ---- 第二步：重叠日做赢家/输家匹配，识别挤占与无法归属残差 ----
    # (category, channel, date) -> [(promo_id, DayTerms), ...]
    overlap_index: dict[tuple, list[tuple[str, DayTerms]]] = {}
    for promo in promos:
        for term in promo.day_terms:
            if term.is_overlap:
                overlap_index.setdefault(
                    (promo.category, promo.channel, term.sale_date), []).append(
                    (promo.promo_id, term))

    overlap_rows: list[dict] = []
    for (category, channel, day), entries in sorted(overlap_index.items()):
        terms = [term for _pid, term in entries]
        winners = [t for t in terms if t.deviation_qty > 0]
        losers = [t for t in terms if t.deviation_qty < 0]
        gain_total = sum((t.deviation_qty for t in winners), Decimal("0"))
        loss_total = -sum((t.deviation_qty for t in losers), Decimal("0"))
        matched = min(gain_total, loss_total)  # 品类内可匹配的转移量
        # 赢家：按观测正偏差占比承接挤占来源（占比来自当天数据，不是固定系数）
        for term in winners:
            share = (term.deviation_qty / gain_total) if gain_total > 0 else Decimal("0")
            term.cannibal_qty = (matched * share).quantize(Q4)
        # 输家：按观测缺口占比记被挤占
        for term in losers:
            share = ((-term.deviation_qty) / loss_total) if loss_total > 0 else Decimal("0")
            term.cannibal_qty = (-matched * share).quantize(Q4)
        # 匹配不掉的净缺口就是无法归属残差，落在具体商品日上，但只在品类层汇总挂账
        for term in terms:
            term.residual_qty = (term.deviation_qty - term.cannibal_qty).quantize(Q4)
        overlap_rows.append({
            "category": category,
            "category_label": config.CATEGORY_LABELS.get(category, category),
            "channel": channel,
            "sale_date": day.isoformat(),
            "promo_count": len(entries),
            "gain_qty": str(gain_total.quantize(Q4)),
            "loss_qty": str(loss_total.quantize(Q4)),
            "matched_qty": str(matched.quantize(Q4)),
            "residual_qty": str((gain_total - loss_total).quantize(Q4)),
            "promo_ids": sorted({pid for pid, _term in entries}),
        })

    # ---- 第三步：非重叠日偏差直接归该商品；汇总并折金额 ----
    for promo in promos:
        for term in promo.day_terms:
            if not term.is_overlap:
                term.residual_qty = term.deviation_qty
            promo.own_qty += term.own_qty
            promo.cannibal_qty += term.cannibal_qty
            promo.residual_qty += term.residual_qty
            promo.lift_qty += (term.actual_qty - term.base_qty)
            promo.days_total += 1
            if term.is_overlap:
                promo.overlap_days += 1
        for key in (OWN, CANNIBAL, RESIDUAL):
            setattr(promo, f"{key}_qty",
                    getattr(promo, f"{key}_qty").quantize(Q4))
        promo.lift_qty = promo.lift_qty.quantize(Q4)
        # 金额：按各分量对应日期的观测价折算；舍入差并入残差
        amount_terms = {OWN: Decimal("0"), CANNIBAL: Decimal("0"),
                        RESIDUAL: Decimal("0")}
        for term in promo.day_terms:
            for key in COMPONENTS:
                amount_terms[key] += getattr(term, f"{key}_qty") * term.price_excl
        total_gmv = coerce.money(Decimal(lift_index[promo.promo_id]["lift_gmv"]))
        split = _money_split(amount_terms, total_gmv)
        promo.own_gmv, promo.cannibal_gmv, promo.residual_gmv = (
            split[OWN], split[CANNIBAL], split[RESIDUAL])
        promo.lift_gmv = total_gmv

    # 品类内挤占销量净额恒为 0；各促销金额分别量化会留下分级舍入差，
    # 统一并入残差（绝不摊回商品的固定比例），保证品类挤占金额净额也为 0。
    groups: dict[str, list[PromoDecomp]] = {}
    for promo in promos:
        groups.setdefault(promo.category, []).append(promo)
    for items in groups.values():
        if len(items) < 2:
            continue
        cannibal_gap = coerce.money(
            sum((p.cannibal_gmv for p in items), Decimal("0")))
        if cannibal_gap:
            anchor = max(items, key=lambda p: abs(p.residual_gmv))
            anchor.residual_gmv = coerce.money(
                anchor.residual_gmv + cannibal_gap)
            anchor.cannibal_gmv = coerce.money(
                anchor.cannibal_gmv - cannibal_gap)

    category_rows = _category_rollup(promos)
    return {
        "promos": promos,
        "overlap_rows": overlap_rows,
        "category_rows": category_rows,
    }


def _category_rollup(promos: list[PromoDecomp]) -> list[dict]:
    """品类层：自身 + 残差计共同增长；挤占净额必须为 0，只计一次。"""
    groups: dict[str, list[PromoDecomp]] = {}
    for promo in promos:
        groups.setdefault(promo.category, []).append(promo)
    out = []
    for category, items in sorted(groups.items()):
        own = sum((p.own_gmv for p in items), Decimal("0"))
        cannibal = sum((p.cannibal_gmv for p in items), Decimal("0"))
        residual = sum((p.residual_gmv for p in items), Decimal("0"))
        lift = sum((p.lift_gmv for p in items), Decimal("0"))
        # 品类共同增长 = 自身增量 + 残差；挤占是内部转移，不进品类口径
        category_lift = coerce.money(own + residual)
        out.append({
            "category": category,
            "category_label": config.CATEGORY_LABELS.get(category, category),
            "promo_count": len(items),
            "own_gmv": str(coerce.money(own)),
            "cannibal_gmv": str(coerce.money(cannibal)),
            "residual_gmv": str(coerce.money(residual)),
            "lift_gmv": str(coerce.money(lift)),
            "category_lift_gmv": str(category_lift),
            "double_count_gmv": str(coerce.money(lift - category_lift)),
            "own_qty": str(sum((p.own_qty for p in items), Decimal("0")).quantize(Q4)),
            "cannibal_qty": str(sum((p.cannibal_qty for p in items), Decimal("0")).quantize(Q4)),
            "residual_qty": str(sum((p.residual_qty for p in items), Decimal("0")).quantize(Q4)),
            "lift_qty": str(sum((p.lift_qty for p in items), Decimal("0")).quantize(Q4)),
        })
    return out


def _exp_factor(slope: Decimal, dlnp: float) -> float:
    return math.exp(float(slope) * dlnp)


def _daterange(start: dt.date, end: dt.date):
    cursor = start
    while cursor < end:
        yield cursor
        cursor += dt.timedelta(days=1)


def as_promo_rows(result: dict) -> list[dict]:
    return [{
        "promo_id": p.promo_id, "sku": p.sku, "channel": p.channel,
        "category": p.category,
        "category_label": config.CATEGORY_LABELS.get(p.category, p.category),
        "days_total": p.days_total, "overlap_days": p.overlap_days,
        "own_qty": str(p.own_qty), "cannibal_qty": str(p.cannibal_qty),
        "residual_qty": str(p.residual_qty), "lift_qty": str(p.lift_qty),
        "own_gmv": str(p.own_gmv), "cannibal_gmv": str(p.cannibal_gmv),
        "residual_gmv": str(p.residual_gmv), "lift_gmv": str(p.lift_gmv),
    } for p in result["promos"]]
