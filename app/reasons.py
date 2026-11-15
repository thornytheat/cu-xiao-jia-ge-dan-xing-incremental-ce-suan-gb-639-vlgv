"""原因码唯一来源：跨模块共享，页面 / 导出 / 测试都从这里取文案。

一个原因码 = 级别 + 所属阶段 + 中文结论 + 复核动作。级别只有三档：
info（记录，可忽略）、warn（需人工看一眼）、error（已隔离，不进测算）。
"""
from __future__ import annotations

from typing import NamedTuple

INFO = "info"
WARN = "warn"
ERROR = "error"
LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}
LEVEL_LABELS = {ERROR: "严重", WARN: "警告", INFO: "提示"}


class Reason(NamedTuple):
    code: str
    level: str
    stage: str
    title: str
    hint: str


def _r(code, level, stage, title, hint):
    return Reason(code, level, stage, title, hint)


REASONS = {
    # ---- M1 采信 ----
    "E_DECODE_FALLBACK": _r(
        "E_DECODE_FALLBACK", WARN, "ingest",
        "声明编码解码失败，已按回退链改用下一档", "确认导出系统实际使用的字符集"),
    "E_BOM_STRIPPED": _r(
        "E_BOM_STRIPPED", INFO, "ingest",
        "文件头带 UTF-8 BOM，已剥离", "首列名不需要带不可见字符"),
    "E_DUP_HEADER": _r(
        "E_DUP_HEADER", WARN, "ingest",
        "文件中出现重复表头，已按分段剔除", "分段导出会在每段前重复一行表头"),
    "E_COLUMN_COUNT": _r(
        "E_COLUMN_COUNT", ERROR, "ingest",
        "该行列数与表头不一致", "多为末尾多一个分隔符或末行被截断，回源核对"),
    "E_TRUNCATED_TAIL": _r(
        "E_TRUNCATED_TAIL", ERROR, "ingest",
        "末行不完整（半行或引号未闭合）", "重新导出完整文件后再导入"),
    "E_HEADER_ONLY": _r(
        "E_HEADER_ONLY", WARN, "ingest",
        "文件只有表头，没有数据行", "确认导出条件是否把数据过滤掉了"),
    "E_EMPTY_FILE": _r(
        "E_EMPTY_FILE", WARN, "ingest",
        "文件为 0 字节", "确认导出任务是否成功"),
    "E_HEADER_UNKNOWN": _r(
        "E_HEADER_UNKNOWN", ERROR, "ingest",
        "表头缺少必需列", "先确认列名，中英文别名都认"),
    "E_FILE_MISSING": _r(
        "E_FILE_MISSING", ERROR, "ingest",
        "样例文件不存在", "检查 sample_data 目录是否挂载"),
    # ---- M2 归价 ----
    "P_SENTINEL_PRICE": _r(
        "P_SENTINEL_PRICE", ERROR, "price",
        "单价是空值哨兵（- / N/A / 未知 等）", "哨兵不是 0，不能参与计算，需回源补价"),
    "P_PRICE_ZERO": _r(
        "P_PRICE_ZERO", ERROR, "price",
        "单价为 0 或负数", "促销价未填最常见，导入前必须补齐"),
    "P_PRICE_LOW": _r(
        "P_PRICE_LOW", ERROR, "price",
        "单价低于常规价 35%，疑似漏小数点", "常见 9.90 写成 0.99"),
    "P_PRICE_HIGH": _r(
        "P_PRICE_HIGH", WARN, "price",
        "单价高于常规价，促销不应提价", "确认是否是换规格或错行"),
    "P_UNIT_CONVERTED": _r(
        "P_UNIT_CONVERTED", INFO, "price",
        "单价按箱/打计价，已折成元/件", "折算系数见盒规表"),
    "P_TAX_CONVERTED": _r(
        "P_TAX_CONVERTED", INFO, "price",
        "含税价已按税率折成不含税价", "销售额一律按不含税金额比较"),
    "P_TAX_UNKNOWN": _r(
        "P_TAX_UNKNOWN", WARN, "price",
        "税率不在白名单，按默认 13% 折算", "补进税率表后重跑"),
    "P_SENTINEL_QTY": _r(
        "P_SENTINEL_QTY", ERROR, "price",
        "销量是空值哨兵，已隔离", "哨兵不是 0 销量"),
    "P_QTY_NEGATIVE": _r(
        "P_QTY_NEGATIVE", ERROR, "price",
        "销量为负数", "退货单独成行，不参与促销测算"),
    # ---- M3 质量 ----
    "Q_MISSING_DAY": _r(
        "Q_MISSING_DAY", WARN, "quality",
        "促销期内该日无记录", "缺日不当 0 销量，单独计入天数账"),
    "Q_BAD_DATE": _r(
        "Q_BAD_DATE", ERROR, "quality",
        "日期无法解析为自然日", "支持 yyyy-mm-dd / yyyy/mm/dd / yyyymmdd"),
    "Q_MISSING_FIELD": _r(
        "Q_MISSING_FIELD", ERROR, "quality",
        "必需字段为空（渠道 / 商品编码 / 销量）", "补值后重导，禁止用默认值顶替"),
    "Q_DUP_DAY": _r(
        "Q_DUP_DAY", ERROR, "quality",
        "同一商品同一渠道同一天出现多条记录", "保留首见行，其余进隔离区"),
    "Q_OUT_OF_WINDOW": _r(
        "Q_OUT_OF_WINDOW", WARN, "quality",
        "日期落在建模窗口之外", "窗口 [起始日, 结束日] 由配置固定"),
    "Q_LOW_COVERAGE": _r(
        "Q_LOW_COVERAGE", ERROR, "quality",
        "有效天数占比低于 70%，不参与测算", "补数或缩短促销期后重跑"),
    "Q_PROMO_OVERLAP": _r(
        "Q_PROMO_OVERLAP", WARN, "quality",
        "同一促销两个版本的活动价区间重叠", "按版本号大者生效，重叠日标出来"),
    "Q_PROMO_NO_VERSION": _r(
        "Q_PROMO_NO_VERSION", ERROR, "quality",
        "促销没有任何可生效版本", "作废与待审版本不能参与测算"),
    "Q_PROMO_BAD_RANGE": _r(
        "Q_PROMO_BAD_RANGE", ERROR, "quality",
        "促销起止日期无法解析或结束不晚于开始", "左闭右开区间要求结束日晚于开始日"),
    "Q_PROMO_UNKNOWN_SKU": _r(
        "Q_PROMO_UNKNOWN_SKU", ERROR, "quality",
        "促销指向的商品在日销文件中不存在", "先补商品的日销记录"),
    "Q_PROMO_GAP": _r(
        "Q_PROMO_GAP", WARN, "quality",
        "相邻两版之间有空档日", "空档日按常规价计，需确认是否符合意图"),
    # ---- M4 弹性 ----
    "L_ZERO_QTY_SHIFTED": _r(
        "L_ZERO_QTY_SHIFTED", INFO, "elastic",
        "0 销量按 ln(q+0.5) 平移后入模", "平移只影响该点，不改变其余样本"),
    "L_ZERO_QTY_DROPPED": _r(
        "L_ZERO_QTY_DROPPED", WARN, "elastic",
        "0 销量按剔除处理，未入模", "剔除会缩小样本，结果需与平移方式对照"),
    "L_SAMPLE_TOO_SMALL": _r(
        "L_SAMPLE_TOO_SMALL", ERROR, "elastic",
        "入模样本少于最小样本数", "至少 6 个有效价格点才能估计"),
    "L_NO_PRICE_VARIATION": _r(
        "L_NO_PRICE_VARIATION", ERROR, "elastic",
        "样本内价格没有变化，斜率不可识别", "促销期价格需与基线期不同"),
    "L_NOT_SIGNIFICANT": _r(
        "L_NOT_SIGNIFICANT", WARN, "elastic",
        "弹性 t 值绝对值小于 1.96", "区间包含 0，结论只能作参考"),
    # ---- M5 增量 ----
    "A_NO_CONTROL": _r(
        "A_NO_CONTROL", ERROR, "lift",
        "该商品没有配置对照组", "对照组映射写在配置里，缺失则无法算基线"),
    "A_CONTROL_EMPTY": _r(
        "A_CONTROL_EMPTY", ERROR, "lift",
        "对照组在基线窗或促销窗内没有有效记录", "补对照商品日销后重跑"),
    "A_NO_BASELINE": _r(
        "A_NO_BASELINE", ERROR, "lift",
        "促销开始日前 14 天没有有效记录", "基线窗无数据时不算增量"),
    "A_SPREAD_REMAINDER": _r(
        "A_SPREAD_REMAINDER", INFO, "lift",
        "渠道分摊出现尾差，已按最大余额法补齐", "分摊合计恒等于总额"),
    # ---- M6 报告 ----
    "R_NO_BATCH": _r(
        "R_NO_BATCH", WARN, "report",
        "还没有任何批次", "先在导入页跑一次"),
    "R_EMPTY_SELECTION": _r(
        "R_EMPTY_SELECTION", WARN, "report",
        "导出选择为空", "先跑一次测算再导出"),
    # ---- M7 实验设计 ----
    "D_NO_BATCH": _r(
        "D_NO_BATCH", WARN, "design",
        "还没有批次，设计缺少历史序列", "先在导入页跑一次"),
    "D_UNKNOWN_SERIES": _r(
        "D_UNKNOWN_SERIES", ERROR, "design",
        "本批次没有该商品该渠道的日销记录", "换一个出现在日销文件里的商品"),
    "D_BAD_TARGET": _r(
        "D_BAD_TARGET", ERROR, "design",
        "目标效应不可用（≤0、>100% 或无法解析）", "写 0.05 或 5% 都认"),
    "D_BAD_PARAM": _r(
        "D_BAD_PARAM", ERROR, "design",
        "α、功效、预算天数或开始日不在取值范围内", "α 在 0-0.5、功效在 0.5-1、天数 1-180"),
    "D_FLAT_SERIES": _r(
        "D_FLAT_SERIES", ERROR, "design",
        "历史有效天数不足，无法定实验期", "至少 7 天窗口、3 天有效记录"),
    "D_FLAT_PRICE": _r(
        "D_FLAT_PRICE", WARN, "design",
        "价格或销量没有变化，稳健标准误按朴素处理", "换一个有价格变化的序列再比"),
    "D_HETERO": _r(
        "D_HETERO", WARN, "design",
        "残差异方差，稳健标准误高于朴素标准误", "方差已放大，实验期按放大后的算"),
    "D_BUDGET_SHORT": _r(
        "D_BUDGET_SHORT", WARN, "design",
        "预算天数少于所需实验期", "按可识别效应决定是否值得跑"),
    "D_IMPUTE_SENSITIVE": _r(
        "D_IMPUTE_SENSITIVE", WARN, "design",
        "缺日补法改变结论（区间含 0 或所需天数摆动）", "补齐缺日后重算一次"),
    "D_IMPUTE_STABLE": _r(
        "D_IMPUTE_STABLE", INFO, "design",
        "三种缺日补法结论一致", "缺日不影响本设计"),
    "D_CONTROL_OVERLAP": _r(
        "D_CONTROL_OVERLAP", ERROR, "design",
        "候选对照在实验期内有自己的促销", "重叠期对照倍数不可用，换对照或挪实验期"),
    "D_CONTROL_TREND": _r(
        "D_CONTROL_TREND", WARN, "design",
        "候选对照与目标的前置趋势差超过 10%", "趋势不同会把差值算进基线"),
    "D_CONTROL_SHORT": _r(
        "D_CONTROL_SHORT", WARN, "design",
        "候选对照的早段或晚段有效天数不足，趋势不可比", "换数据更完整的对照"),
    "D_SHORT_HISTORY": _r(
        "D_SHORT_HISTORY", WARN, "design",
        "促销开始前的干净窗口太短，趋势改按全窗口比", "补更早的日销再比趋势"),
    "D_CONTROL_PICKED": _r(
        "D_CONTROL_PICKED", INFO, "design",
        "已按前置趋势差选出对照", "换实验期要重新选一次"),
    "D_CONTROL_REGISTERED": _r(
        "D_CONTROL_REGISTERED", INFO, "design",
        "选中的对照与配置里的映射一致", "无需改配置"),
    "D_NO_CONTROL": _r(
        "D_NO_CONTROL", ERROR, "design",
        "没有可用对照候选，基线不可算", "补同渠道同品类商品的日销"),
    "D_WINDOW_OVERLAP": _r(
        "D_WINDOW_OVERLAP", WARN, "design",
        "实验期内本商品自己有促销，窗口不干净", "把开始日挪到促销结束之后"),
}


def get(code: str) -> Reason:
    return REASONS.get(code, Reason(code, WARN, "unknown", code, "未登记的原因码"))


def level_of(code: str) -> str:
    return get(code).level


def label(code: str) -> str:
    return get(code).title


def stage_of(code: str) -> str:
    return get(code).stage


def catalog() -> list[dict]:
    """给 /api/contract 与页面用的原因码目录（按阶段、级别排序）。"""
    rows = sorted(REASONS.values(), key=lambda r: (r.stage, LEVEL_ORDER[r.level], r.code))
    return [
        {"code": r.code, "level": r.level, "stage": r.stage,
         "title": r.title, "hint": r.hint, "level_label": LEVEL_LABELS[r.level]}
        for r in rows
    ]


def is_error(code: str) -> bool:
    return level_of(code) == ERROR


def severity_counts(codes) -> dict:
    out = {ERROR: 0, WARN: 0, INFO: 0}
    for code in codes:
        out[level_of(code)] += 1
    return out
