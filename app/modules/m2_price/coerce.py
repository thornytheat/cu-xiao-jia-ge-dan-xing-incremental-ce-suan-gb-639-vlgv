"""数值归一：全角转半角、千分位、单位后缀、含税折算、哨兵三态。

朴素实现直接 `float(单元格)`：
- `1,234.50` 抛 ValueError；
- `１２.８０` 抛 ValueError；
- `12.80 元/箱` 抛 ValueError；
- `-` / `N/A` / `未知` 被当成 0，合计里悄悄少了这些天。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ... import config

FULLWIDTH = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "．": ".", "，": ",", "　": " ", "－": "-", "﹣": "-",
    "％": "%", "：": ":",
})

UNIT_SUFFIXES = ("元/件", "元/箱", "元/打", "元/盒", "元/袋", "元", "RMB", "¥", "￥")


def halfwidth(text: str) -> str:
    return str(text).translate(FULLWIDTH)


def is_sentinel(value) -> bool:
    if value is None:
        return True
    text = halfwidth(value).strip()
    return text.lower() in config.SENTINELS or text in config.SENTINELS


def clean_number(text) -> str:
    """去掉千分位分隔符、货币符号与计价单位后缀，保留符号与小数点。"""
    value = halfwidth(text).strip()
    for suffix in sorted(UNIT_SUFFIXES, key=len, reverse=True):
        if value.endswith(suffix):
            value = value[: -len(suffix)].strip()
            break
    value = value.replace(",", "").replace(" ", "").replace("¥", "").replace("￥", "")
    return value


def split_unit(raw, unit_column) -> tuple[str, str]:
    """从价格单元格里拆出包装单位；单元格无后缀时退回单位列。"""
    text = halfwidth(raw).strip()
    for suffix in sorted(UNIT_SUFFIXES, key=len, reverse=True):
        if suffix.startswith("元/") and text.endswith(suffix):
            return text[: -len(suffix)].strip(), suffix.split("/", 1)[1]
    unit = halfwidth(unit_column or "").strip()
    if unit.startswith("元/"):
        unit = unit.split("/", 1)[1]
    return text, unit or "件"


def to_decimal(raw) -> Decimal | None:
    """哨兵返回 None；无法解析返回 None。调用方据原因码决定隔离还是补默认。"""
    if is_sentinel(raw):
        return None
    text = clean_number(raw)
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def pack_size(unit: str) -> Decimal:
    return config.PACK_SIZE.get((unit or "件").strip(), Decimal("1"))


def to_price_per_piece(raw, unit_column="", *, base_unit="件") -> tuple[Decimal | None, list[str]]:
    """含税单价 → 元/件（含税）。返回 (价格, 提示码)。"""
    notes: list[str] = []
    number, unit = split_unit(raw, unit_column)
    value = to_decimal(number)
    if value is None:
        return None, notes
    if unit and unit != base_unit:
        size = pack_size(unit)
        if size > 1:
            value = value / size
            notes.append("P_UNIT_CONVERTED")
    return value.quantize(config.PRICE_QUANT, rounding=ROUND_HALF_UP), notes


def to_tax_rate(raw) -> tuple[Decimal, str | None]:
    if is_sentinel(raw):
        return config.DEFAULT_TAX, "P_TAX_UNKNOWN"
    text = halfwidth(raw).strip()
    if text in config.TAX_RATES:
        return config.TAX_RATES[text], None
    number = to_decimal(text)
    if number is None:
        return config.DEFAULT_TAX, "P_TAX_UNKNOWN"
    if number > 1:
        number = number / Decimal("100")
    key = str(number.quantize(Decimal("0.01")))
    if key in config.TAX_RATES:
        return config.TAX_RATES[key], None
    return config.DEFAULT_TAX, "P_TAX_UNKNOWN"


def excl_of(price_incl: Decimal, rate: Decimal) -> Decimal:
    return (price_incl / (Decimal("1") + rate)).quantize(
        config.PRICE_QUANT, rounding=ROUND_HALF_UP)


def to_qty(raw) -> int | None:
    if is_sentinel(raw):
        return None
    text = clean_number(raw)
    if not text:
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return int(value.quantize(config.QTY_QUANT, rounding=ROUND_HALF_UP))


DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y年%m月%d日")


def to_date(raw) -> dt.date | None:
    if is_sentinel(raw):
        return None
    text = halfwidth(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def money(value: Decimal) -> Decimal:
    return value.quantize(config.MONEY_QUANT, rounding=ROUND_HALF_UP)
