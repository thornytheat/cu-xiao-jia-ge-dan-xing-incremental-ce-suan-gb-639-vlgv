"""归价编排：日销行归一 → 常规价对照 → 促销版本解析 → 返回可入模行。

两步走：先把价格折成「元/件、不含税」，再按同商品同渠道的非促销日算出常规价，
用常规价判定「异常低 / 异常高」。一步内完成会因为常规价还没算出来而漏判。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from ... import config
from . import coerce, versions


@dataclass
class SaleRow:
    line_no: int
    sale_date: object
    channel: str
    sku: str
    sku_name: str
    price_incl: Decimal
    price_excl: Decimal
    tax_rate: Decimal
    qty: int
    promo_id: str
    price_unit: str
    amount_excl: Decimal
    flags: list[str] = field(default_factory=list)


def _q(row, code, stage="price", field_name="", raw="", detail="") -> dict:
    return {"code": code, "stage": stage, "source": row.get("source", "daily_sales"),
            "line_no": row.get("line_no"), "field": field_name,
            "raw_value": raw, "detail": detail}


def _drop(dropped: list[dict], row, code: str) -> None:
    dropped.append({"sale_date": row.sale_date, "sku": row.sku,
                    "channel": row.channel, "code": code, "line_no": row.line_no})


def parse_sales(parsed, source_name: str = "daily_sales"
                ) -> tuple[list[SaleRow], list[dict], list[dict]]:
    rows: list[SaleRow] = []
    quarantine: list[dict] = []
    dropped: list[dict] = []
    for record in parsed.records:
        data = record.data
        base = {"source": source_name, "line_no": record.line_no}
        if record.code:
            quarantine.append(_q({**base, "code": record.code}, "E_COLUMN_COUNT",
                                 stage="ingest", field_name="整行",
                                 raw=",".join(record.raw_fields),
                                 detail=(f"本行 {len(record.raw_fields)} 列，"
                                         f"表头 {len(parsed.header.raw)} 列")))
            continue
        sale_date = coerce.to_date(data.get("日期"))
        if sale_date is None:
            quarantine.append(_q(base, "Q_BAD_DATE", field_name="日期",
                                 raw=str(data.get("日期")), detail="无法解析为自然日"))
            continue
        channel = (data.get("渠道") or "").strip()
        sku = (data.get("商品编码") or "").strip()
        if not channel or not sku:
            quarantine.append(_q(base, "Q_MISSING_FIELD", field_name="渠道/商品编码",
                                 raw=f"{channel}|{sku}", detail="必需字段为空"))
            continue
        price_incl, notes = coerce.to_price_per_piece(
            data.get("含税单价"), data.get("单价单位", ""))
        if price_incl is None:
            quarantine.append(_q(base, "P_SENTINEL_PRICE", field_name="含税单价",
                                 raw=str(data.get("含税单价")),
                                 detail="单价不可用，哨兵或无法解析"))
            continue
        if price_incl <= 0:
            quarantine.append(_q(base, "P_PRICE_ZERO", field_name="含税单价",
                                 raw=str(price_incl), detail="单价为 0"))
            dropped.append({"sale_date": sale_date, "sku": sku, "channel": channel,
                            "code": "P_PRICE_ZERO", "line_no": record.line_no})
            continue
        qty = coerce.to_qty(data.get("销量"))
        if qty is None:
            quarantine.append(_q(base, "P_SENTINEL_QTY", field_name="销量",
                                 raw=str(data.get("销量")), detail="销量是空值哨兵"))
            dropped.append({"sale_date": sale_date, "sku": sku, "channel": channel,
                            "code": "P_SENTINEL_QTY", "line_no": record.line_no})
            continue
        if qty < 0:
            quarantine.append(_q(base, "P_QTY_NEGATIVE", field_name="销量",
                                 raw=str(qty), detail="销量为负"))
            dropped.append({"sale_date": sale_date, "sku": sku, "channel": channel,
                            "code": "P_QTY_NEGATIVE", "line_no": record.line_no})
            continue
        rate, tax_note = coerce.to_tax_rate(data.get("税率"))
        flags = list(notes)
        if tax_note:
            flags.append(tax_note)
        price_excl = coerce.excl_of(price_incl, rate)
        rows.append(SaleRow(
            line_no=record.line_no, sale_date=sale_date, channel=channel, sku=sku,
            sku_name=(data.get("商品名称") or "").strip(),
            price_incl=price_incl, price_excl=price_excl, tax_rate=rate, qty=qty,
            promo_id=(data.get("促销编号") or "").strip(),
            price_unit=data.get("单价单位", "") or "元/件",
            amount_excl=coerce.money(price_excl * Decimal(qty)),
            flags=flags))
    return rows, quarantine, dropped


def base_prices(rows: list[SaleRow]) -> dict[tuple[str, str], Decimal]:
    """常规价 = 同商品同渠道非促销日含税单价的中间值（抗异常值）。"""
    buckets: dict[tuple[str, str], list[Decimal]] = {}
    for row in rows:
        if row.promo_id:
            continue
        buckets.setdefault((row.sku, row.channel), []).append(row.price_incl)
    return {key: median(values) for key, values in buckets.items() if values}


def base_prices_excl(rows: list[SaleRow]) -> dict[tuple[str, str], Decimal]:
    """常规不含税单价 = 非促销日不含税单价的中间值。"""
    buckets: dict[tuple[str, str], list[Decimal]] = {}
    for row in rows:
        if row.promo_id:
            continue
        buckets.setdefault((row.sku, row.channel), []).append(row.price_excl)
    return {key: median(values) for key, values in buckets.items() if values}


def flag_outliers(rows: list[SaleRow], base: dict[tuple[str, str], Decimal]):
    """常规价出来之后再做异常价判定：低于 35% 或促销期高于常规价都拦下。"""
    kept: list[SaleRow] = []
    quarantine: list[dict] = []
    dropped: list[dict] = []
    for row in rows:
        reference = base.get((row.sku, row.channel))
        if reference:
            ratio = row.price_incl / reference
            if ratio < config.PRICE_LOW_RATIO:
                quarantine.append({
                    "code": "P_PRICE_LOW", "stage": "price",
                    "source": "daily_sales", "line_no": row.line_no,
                    "field": "含税单价", "raw_value": str(row.price_incl),
                    "detail": f"{row.sku}/{row.channel} 常规价 {reference}，本行 {ratio:.2%}",
                })
                _drop(dropped, row, "P_PRICE_LOW")
                continue
            if row.promo_id and ratio > config.PRICE_HIGH_RATIO:
                quarantine.append({
                    "code": "P_PRICE_HIGH", "stage": "price",
                    "source": "daily_sales", "line_no": row.line_no,
                    "field": "含税单价", "raw_value": str(row.price_incl),
                    "detail": f"{row.sku}/{row.channel} 促销价高于常规价 {ratio:.2%}",
                })
                _drop(dropped, row, "P_PRICE_HIGH")
                continue
        kept.append(row)
    return kept, quarantine, dropped


def normalize(loaded: dict) -> dict:
    files = loaded["files"]
    sales_rows, sales_q, sales_drop = parse_sales(files["sales"], "daily_sales")
    control_rows, control_q, control_drop = parse_sales(files["control"], "control_group")
    base = base_prices(sales_rows)
    sales_rows, low_q, low_drop = flag_outliers(sales_rows, base)
    control_rows, control_low_q, _ = flag_outliers(control_rows, base_prices(control_rows))

    promo = versions.build(files["promo"].records)
    quarantine = sales_q + low_q + control_q + control_low_q + promo["quarantine"]
    # 采信阶段的编码 / BOM / 分段提示逐文件落到隔离区，便于复核
    for role in ("sales", "promo", "control"):
        parsed = files[role]
        for code in parsed.codes:
            if code in ("E_TRUNCATED_TAIL", "E_HEADER_UNKNOWN"):
                continue
            quarantine.append({
                "code": code, "stage": "ingest", "source": role,
                "line_no": 1, "field": "文件头",
                "raw_value": parsed.encoding or parsed.name,
                "detail": f"{parsed.name} 声明 {parsed.declared or '无'} / 实际 {parsed.encoding or '—'}",
            })

    for row in sales_rows:
        for code in row.flags:
            quarantine.append({
                "code": code, "stage": "price", "source": "daily_sales",
                "line_no": row.line_no, "field": "含税单价",
                "raw_value": str(row.price_incl),
                "detail": f"{row.sku}/{row.channel} 折算提示",
            })

    return {
        "sales": sales_rows,
        "control": control_rows,
        "base_prices": base,
        "base_prices_excl": base_prices_excl(sales_rows),
        "control_base_prices": base_prices(control_rows),
        "control_base_prices_excl": base_prices_excl(control_rows),
        "versions": promo["versions"],
        "windows": promo["window_objs"],
        "quarantine": quarantine,
        "overlap_days": promo["overlap_days"],
        "gap_days": promo["gap_days"],
        "active_promos": promo["active_promos"],
        "inactive_promos": promo["inactive_promos"],
        "pending_promos": promo["pending_promos"],
        "blocked_promos": promo["blocked_promos"],
        "promo_versions": promo["promo_versions"],
        "ingest_quarantine": sales_q,
        "dropped": sales_drop + low_drop,
        "records_total": files["sales"].rows,
    }


def window_rows(windows, base: dict[tuple[str, str], Decimal]) -> list[dict]:
    rows = []
    for window in windows:
        reference = base.get((window.sku, window.channel))
        rows.append({
            "promo_id": window.promo_id, "sku": window.sku, "channel": window.channel,
            "start_date": window.start.isoformat(), "end_date": window.end.isoformat(),
            "version": window.version, "price_incl": str(window.price_incl),
            "price_excl": str(window.price_excl), "days": window.days,
            "base_price_excl": str(reference if reference is not None else 0),
        })
    return rows
