"""模板渲染与共享视图上下文：过滤器、导航、当前批次选择。

页面只读 store，不做业务计算；数字格式化统一在这里，避免每页各写一套。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config, reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

NAV = (
    ("/", "总览", "ingest"),
    ("/contract", "含税与归价", "price"),
    ("/quarantine", "隔离区", "quality"),
    ("/elasticity", "弹性", "elastic"),
    ("/lift", "增量", "lift"),
    ("/design", "实验设计", "design"),
    ("/report", "报告", "report"),
)
STATUS_LABELS = {
    "done": "完成", "failed": "失败", "running": "运行中", "ready": "就绪",
}
ROLE_LABELS = {"sales": "日销", "promo": "促销日历", "control": "对照组"}


@dataclass
class Ctx:
    settings: config.Settings
    store: object

    def current_batch(self, batch_id: str | None = None) -> dict | None:
        if batch_id:
            return self.store.get_batch(batch_id)
        return self.store.latest_batch()


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def setup(app) -> None:
    t = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = t.env
    env.filters["money"] = fmt_money
    env.filters["qty"] = fmt_qty
    env.filters["price"] = fmt_price
    env.filters["pct"] = fmt_pct
    env.filters["num4"] = fmt_num4
    env.filters["level"] = lambda s: reasons.LEVEL_LABELS.get(s, s or "—")
    env.filters["sev"] = lambda s: reasons.LEVEL_LABELS.get(s, s or "—")
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s or "—")
    env.filters["shortdt"] = lambda s: (s or "").replace("T", " ")[:19]
    env.globals.update(
        nav=NAV,
        stage_flow=config.STAGE_FLOW,
        stage_labels=config.STAGE_LABELS,
        stage_keys=config.STAGES,
        reason_catalog=reasons.catalog(),
        reasons=reasons.REASONS,
        level_labels=reasons.LEVEL_LABELS,
        money_unit=config.MONEY_UNIT,
        qty_unit=config.QTY_UNIT,
        price_unit=config.PRICE_UNIT_BASE,
        zero_modes=config.ZERO_MODES,
        significance_t=str(config.SIGNIFICANCE_T),
        min_coverage=str(config.MIN_COVERAGE_RATIO),
        baseline_window=config.BASELINE_WINDOW_DAYS,
        control_map=config.CONTROL_MAP,
        datasets=config.DATASETS,
        generated_at=now_local().strftime("%Y-%m-%d %H:%M:%S"),
        timezone_label="Asia/Shanghai (+08:00)",
        role_labels=ROLE_LABELS,
    )
    app.state.templates = t


def render(request: Request, name: str, **context):
    ctx: Ctx = request.app.state.ctx
    batch = context.pop("batch", None)
    if batch is None:
        batch = ctx.current_batch(context.get("batch_id"))
    stage_rows = context.pop("stages", None)
    if stage_rows is None and batch is not None:
        stage_rows = ctx.store.stages(batch["batch_id"])
    base = {
        "request": request,
        "stages": stage_rows or [],
        "ctx": ctx,
        "settings": ctx.settings,
        "batch": batch,
        "rule_version": ctx.settings.rule_version,
        "print_ts": now_local().strftime("%Y-%m-%d %H:%M"),
    }
    base.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=base)


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CHINA_TZ)


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def fmt_money(value) -> str:
    d = _dec(value)
    return "—" if d is None else f"{d:,.2f}"


def fmt_qty(value) -> str:
    d = _dec(value)
    if d is None:
        return "—"
    return f"{d:,.0f}"


def fmt_price(value) -> str:
    d = _dec(value)
    return "—" if d is None else f"{d:,.4f}"


def fmt_num4(value) -> str:
    d = _dec(value)
    return "—" if d is None else f"{d:.4f}"


def fmt_pct(value, digits: int = 1) -> str:
    d = _dec(value)
    if d is None:
        return "—"
    return f"{d * 100:.{digits}f}%"


def signed_money(value) -> str:
    d = _dec(value)
    if d is None:
        return "—"
    sign = "+" if d > 0 else ""
    return f"{sign}{d:,.2f}"
