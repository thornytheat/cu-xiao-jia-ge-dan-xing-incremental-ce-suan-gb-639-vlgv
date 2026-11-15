"""列契约与换算表：列名 / 类型 / 单位 / 精度 / 可空，含税与包装的换算规则。

契约是页面与导出的展示来源，也是测试断言的常量来源，规则值只写在这里。
"""
from __future__ import annotations

from ... import config

FIELDS = (
    {"name": "日期", "type": "date", "unit": "—", "precision": "日", "nullable": False,
     "rule": "支持 yyyy-mm-dd / yyyy/mm/dd / yyyymmdd"},
    {"name": "渠道", "type": "text", "unit": "—", "precision": "—", "nullable": False,
     "rule": "电商 / 商超 / 经销"},
    {"name": "商品编码", "type": "text", "unit": "—", "precision": "—", "nullable": False,
     "rule": "按字符串保形，不做数值化"},
    {"name": "商品名称", "type": "text", "unit": "—", "precision": "—", "nullable": True,
     "rule": "仅展示，不参与计算"},
    {"name": "含税单价", "type": "decimal", "unit": "元/件", "precision": "4 位",
     "nullable": False, "rule": "去千分位与全角后按盒规折成元/件"},
    {"name": "单价单位", "type": "text", "unit": "—", "precision": "—", "nullable": True,
     "rule": "元/件、元/箱（1 箱 = 12 件）、元/打（1 打 = 6 件）"},
    {"name": "税率", "type": "decimal", "unit": "—", "precision": "2 位", "nullable": True,
     "rule": "白名单 0.09 / 0.13；不在白名单按 0.13 并记提示"},
    {"name": "销量", "type": "integer", "unit": "件", "precision": "整数", "nullable": False,
     "rule": "负数隔离；哨兵不当 0"},
    {"name": "促销编号", "type": "text", "unit": "—", "precision": "—", "nullable": True,
     "rule": "空值表示非促销日"},
)

PROMO_FIELDS = (
    {"name": "促销编号", "type": "text", "unit": "—", "precision": "—", "nullable": False,
     "rule": "同一编号可有多个版本"},
    {"name": "版本", "type": "integer", "unit": "—", "precision": "整数", "nullable": False,
     "rule": "同一天被多版覆盖时版本号大者生效"},
    {"name": "开始日期", "type": "date", "unit": "—", "precision": "日", "nullable": False,
     "rule": "区间左端，含当天"},
    {"name": "结束日期", "type": "date", "unit": "—", "precision": "日", "nullable": False,
     "rule": "区间右端，不含当天（左闭右开）"},
    {"name": "活动价", "type": "decimal", "unit": "元/件", "precision": "4 位",
     "nullable": False, "rule": "0 或负数整版隔离"},
    {"name": "状态", "type": "text", "unit": "—", "precision": "—", "nullable": False,
     "rule": "只有「生效」参与测算；作废与待审不生效"},
)

RULES = (
    {"key": "price_chain", "label": "含税转不含税",
     "value": "不含税单价 = 含税单价 ÷ (1 + 税率)，保留 4 位，四舍五入"},
    {"key": "pack", "label": "包装折算",
     "value": "元/箱 ÷ 12、元/打 ÷ 6 折成元/件，折算后再做含税转换"},
    {"key": "window", "label": "促销区间",
     "value": "[开始日期, 结束日期)，相邻两版首尾相接不重复计入"},
    {"key": "version", "label": "版本优先级",
     "value": "同一天被多版覆盖时版本号大者生效，与文件里的书写顺序无关"},
    {"key": "zero_qty", "label": "0 销量",
     "value": "默认统一平移 ln(q + 0.5) 并逐点标注；可切到剔除，剔除数与结论一起展示"},
    {"key": "baseline", "label": "基线",
     "value": "促销前 14 天日均 × 促销有效天数 × 对照组同期增长倍数"},
    {"key": "amount", "label": "销售额",
     "value": "不含税单价 × 件数，Decimal 累加后统一保留 2 位"},
)


def fields() -> list[dict]:
    return [dict(row) for row in FIELDS]


def promo_fields() -> list[dict]:
    return [dict(row) for row in PROMO_FIELDS]


def rules() -> list[dict]:
    return [dict(row) for row in RULES]


def tax_table() -> list[dict]:
    return [{"raw": raw, "rate": str(rate)} for raw, rate in config.TAX_RATES.items()]


def pack_table() -> list[dict]:
    return [{"unit": unit, "pieces": str(size)} for unit, size in config.PACK_SIZE.items()]


def sentinels() -> list[str]:
    return sorted(config.SENTINELS)


def contract() -> dict:
    return {
        "rule_version": config.RULE_VERSION,
        "price_unit": config.PRICE_UNIT_BASE,
        "money_unit": config.MONEY_UNIT,
        "qty_unit": config.QTY_UNIT,
        "decimals": {"money": 2, "price": 4, "qty": 0, "ratio": 4},
        "fields": fields(),
        "promo_fields": promo_fields(),
        "rules": rules(),
        "tax_rates": tax_table(),
        "pack_sizes": pack_table(),
        "sentinels": sentinels(),
        "zero_modes": list(config.ZERO_MODES),
        "min_coverage_ratio": str(config.MIN_COVERAGE_RATIO),
        "min_samples": config.MIN_SAMPLES,
        "significance_t": str(config.SIGNIFICANCE_T),
        "baseline_window_days": config.BASELINE_WINDOW_DAYS,
        "control_map": dict(config.CONTROL_MAP),
    }
