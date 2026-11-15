"""pricelift 全局配置：编码回退链、单位表、税率、箱规、促销优先级。

规则唯一来源，其他模块只读不写：
- 单价一律折成「元/件、不含税」，Decimal 保存，最小刻度 0.01；
- 销量一律「件」，整数；
- 销售额 = 单件不含税价 × 件数，Decimal 累加，2 位小数；
- 促销区间左闭右开 [开始日期, 结束日期)，同日多版本时版本号大者生效。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "pricelift/1.4.0"
MONEY_QUANT = Decimal("0.01")
QTY_QUANT = Decimal("1")
PRICE_QUANT = Decimal("0.0001")

PRICE_UNIT_BASE = "元/件"
MONEY_UNIT = "元"
QTY_UNIT = "件"

STAGES = ("ingest", "price", "quality", "elastic", "lift", "report")
STAGE_LABELS = {
    "ingest": "采信",
    "price": "归价",
    "quality": "质量",
    "elastic": "弹性",
    "lift": "增量",
    "design": "设计",
    "report": "报告",
}
STAGE_FLOW = "采信 → 归价 → 质量 → 弹性 → 增量 → 报告"

# 编码回退链：声明只作首个候选，严格解码失败即回退；gb18030 是 gbk/gb2312 的超集
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")
CHARSET_ALIASES = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8-sig",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
}

# 空值哨兵：集中定义；哨兵不是 0，不能参与合计
SENTINELS = frozenset(
    {"", "-", "--", "—", "——", "/", "n/a", "na", "null", "none", "nil",
     "未知", "无", "暂无", "待定", "不适用", "未填", "缺"}
)

# 单件容量（件/包装单位）：价格若以包装计价，先除以盒规折成元/件
PACK_SIZE = {
    "件": Decimal("1"),
    "袋": Decimal("1"),
    "盒": Decimal("1"),
    "箱": Decimal("12"),
    "打": Decimal("6"),
}

# 税率白名单：0.09 / 0.13 为本业务在用；其它值需人工确认
TAX_RATES = {
    "0.09": Decimal("0.09"),
    "0.13": Decimal("0.13"),
    "9%": Decimal("0.09"),
    "13%": Decimal("0.13"),
    "0.09（9%）": Decimal("0.09"),
}
DEFAULT_TAX = Decimal("0.13")

# 价格合理性：低于常规价 35% 视为异常低（疑似漏小数点或错位）
PRICE_LOW_RATIO = Decimal("0.35")
# 促销价高于常规价视为异常（提价促销无业务含义）
PRICE_HIGH_RATIO = Decimal("1.05")

# 促销状态：只有「生效」参与测算
PROMO_STATUS_ACTIVE = "生效"
PROMO_STATUS_VOID = "作废"
PROMO_STATUS_DRAFT = "待审"
PROMO_STATUS_LABELS = {
    PROMO_STATUS_ACTIVE: "生效",
    PROMO_STATUS_VOID: "作废",
    PROMO_STATUS_DRAFT: "待审",
}

# 0 销量处理方式
ZERO_MODE_SHIFT = "shift"      # ln(q + 0.5)
ZERO_MODE_DROP = "drop"        # 剔除并单独计数
ZERO_MODES = (ZERO_MODE_SHIFT, ZERO_MODE_DROP)
ZERO_SHIFT = Decimal("0.5")

# 建模最少样本与非显著性阈值
MIN_SAMPLES = 6
SIGNIFICANCE_T = Decimal("1.96")

# 基线窗口：促销开始日前 N 天作为基线观察窗
BASELINE_WINDOW_DAYS = 14
# 缺日容忍：有效天数 / 应覆盖天数 低于该比例则不测算
MIN_COVERAGE_RATIO = Decimal("0.70")

# 商品与对照组的映射（同品类未促销 SKU）
CONTROL_MAP = {
    "MY-0012": "MY-0019",
    "MY-0021": "MY-0028",
}

DATASETS = {
    "main": {
        "sales": "daily_sales_gb18030.csv",
        "promo": "promo_calendar.csv",
        "control": "control_group.csv",
        "label": "主样例（GB18030 + 缺日 + 重复日）",
    },
    "utf8bom": {
        "sales": "daily_sales_utf8bom.csv",
        "promo": "promo_calendar.csv",
        "control": "control_group.csv",
        "label": "BOM 分段样例（引号内换行 + 重复表头）",
    },
    "truncated": {
        "sales": "truncated_tail.csv",
        "promo": "promo_calendar.csv",
        "control": "control_group.csv",
        "label": "截断样例（末行半行）",
    },
    "header_only": {
        "sales": "header_only.csv",
        "promo": "promo_calendar.csv",
        "control": "control_group.csv",
        "label": "仅表头样例",
    },
    "empty": {
        "sales": "empty.csv",
        "promo": "promo_calendar.csv",
        "control": "control_group.csv",
        "label": "空文件样例",
    },
}

# 列契约：库里是中文列名；别名表支持门店系统导出的英文表头
COLUMNS = (
    ("日期", "date", "sale_date", "业务日期"),
    ("渠道", "channel", "渠道类型"),
    ("商品编码", "sku", "商品代码", "货号"),
    ("商品名称", "sku_name", "品名"),
    ("含税单价", "price_incl", "含税售价", "单价"),
    ("单价单位", "price_unit", "计价单位"),
    ("税率", "tax_rate", "税率%"),
    ("销量", "qty", "数量", "销售数量"),
    ("促销编号", "promo_id", "活动编号"),
    # 促销日历专用列
    ("版本", "version"),
    ("开始日期", "start_date"),
    ("结束日期", "end_date"),
    ("活动价", "activity_price"),
    ("状态", "status"),
    ("备注", "note"),
)
REQUIRED_COLUMNS = ("日期", "渠道", "商品编码", "含税单价", "销量")
PROMO_REQUIRED_COLUMNS = ("促销编号", "商品编码", "渠道", "版本",
                          "开始日期", "结束日期", "活动价", "状态")
COLUMN_ALIASES = {}
for _group in COLUMNS:
    _canonical = _group[0]
    for _alias in _group[1:]:
        COLUMN_ALIASES[_alias.lower()] = _canonical
    COLUMN_ALIASES[_canonical] = _canonical


@dataclass(frozen=True)
class Settings:
    """运行参数；测试用 replace() 覆盖，不读环境变量的分支。"""

    db_path: str = str(PROJECT_ROOT / "var" / "pricelift.sqlite")
    sample_dir: str = str(PROJECT_ROOT / "sample_data")
    export_dir: str = str(PROJECT_ROOT / "var" / "export")
    rule_version: str = RULE_VERSION
    zero_mode: str = ZERO_MODE_SHIFT

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        db_path = os.getenv("PRICELIFT_DB") or base.db_path
        sample_dir = os.getenv("PRICELIFT_SAMPLE_DIR") or base.sample_dir
        export_dir = os.getenv("PRICELIFT_EXPORT_DIR") or base.export_dir
        zero_mode = os.getenv("PRICELIFT_ZERO_MODE") or base.zero_mode
        if zero_mode not in ZERO_MODES:
            zero_mode = ZERO_MODE_SHIFT
        return replace(cls(), db_path=db_path, sample_dir=sample_dir,
                       export_dir=export_dir, zero_mode=zero_mode)

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent

    def sample_path(self, name: str) -> Path:
        return Path(self.sample_dir).resolve() / name
