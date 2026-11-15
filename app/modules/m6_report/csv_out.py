"""导出 CSV：证据行带原值与原因码；数字按最小单位输出，不带千分位。

导出的 CSV 自带 UTF-8 BOM（Excel 直接双击不乱码），本系统的导入端会剥离 BOM，
两边配合得上——这也是 TRAP-02 的现实来源。
"""
from __future__ import annotations

import csv
import io

BOM = "\ufeff"

LIFT_HEADER = ("促销编号", "商品编码", "渠道", "开始日期", "结束日期", "促销天数",
               "有效天数", "缺日", "剔除", "实际销量", "基线销量", "实际销售额",
               "基线销售额", "增量销量", "增量销售额", "区间下限", "区间上限",
               "促销价(不含税)", "基线价(不含税)", "对照商品", "对照倍数", "价格变化 ln",
               "弹性", "结论")

SALES_HEADER = ("行号", "日期", "渠道", "商品编码", "商品名称", "含税单价",
                "单价单位", "税率", "不含税单价", "销量", "销售额", "促销编号")

ELASTIC_HEADER = ("范围", "分组", "样本数", "0 销量条数", "截距", "弹性",
                  "标准误", "t 值", "R²", "0 销量处理", "结论")

RECONCILE_HEADER = ("账目", "左值", "右值", "差额", "结论", "说明")


def _write(header, rows) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return BOM + buf.getvalue()


def lift_csv(rows) -> str:
    body = []
    for row in rows:
        body.append((
            row["promo_id"], row["sku"], row["channel"], row["start_date"],
            row["end_date"], row["days"], row["days_valid"], row["days_missing"],
            row["days_dropped"], row["actual_qty"], row["base_qty"],
            row["actual_gmv"], row["base_gmv"], row["lift_qty"], row["lift_gmv"],
            row["ci_low"], row["ci_high"], row["price_excl"], row["base_price_excl"],
            row["control_sku"], row["control_factor"], row["dlnp"], row["slope"],
            row["note"],
        ))
    return _write(LIFT_HEADER, body)


def sales_csv(rows) -> str:
    body = []
    for row in rows:
        body.append((
            row["line_no"], row["sale_date"], row["channel"], row["sku"],
            row["sku_name"], row["price_incl"], row["price_unit"], row["tax_rate"],
            row["price_excl"], row["qty"], row["amount_excl"], row["promo_id"],
        ))
    return _write(SALES_HEADER, body)


def elastic_csv(rows) -> str:
    body = []
    for row in rows:
        body.append((
            row["scope"], row["group_key"], row["n"], row["zero_count"],
            row["intercept"], row["slope"], row["se"], row["tvalue"], row["r2"],
            row["zero_mode"], row["note"],
        ))
    return _write(ELASTIC_HEADER, body)


def reconcile_csv(rows) -> str:
    body = []
    for row in rows:
        body.append((row["account"], row["left"], row["right"], row["delta"],
                     row["verdict"], row["note"]))
    return _write(RECONCILE_HEADER, body)


KINDS = {
    "lift": ("增量明细", lift_csv),
    "sales": ("入模日销", sales_csv),
    "elastic": ("弹性结果", elastic_csv),
    "reconcile": ("账目核对", reconcile_csv),
}


def kind_label(kind: str) -> str:
    return KINDS.get(kind, ("未知", lift_csv))[0]


def render(kind: str, rows) -> str:
    _, func = KINDS.get(kind, ("", lift_csv))
    return func(rows)
