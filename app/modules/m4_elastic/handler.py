"""M4 入口：弹性结果 API 与弹性页。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ... import web
from . import fit, service

router = APIRouter()


def _batch_id(request: Request) -> str | None:
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    return batch["batch_id"] if batch else None


@router.get("/api/elasticity")
def api_elasticity(request: Request) -> dict:
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    if batch_id is None:
        return {"batch_id": None, "rows": [], "headline": None}
    rows = ctx.store.elasticity(batch_id)
    return {
        "batch_id": batch_id,
        "rows": rows,
        "headline": fit.headline(rows),
        "zero_mode": ctx.settings.zero_mode,
    }


@router.get("/elasticity")
def page_elasticity(request: Request):
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    rows = ctx.store.elasticity(batch_id) if batch_id else []
    sku_rows = [r for r in rows if r["scope"] == "sku"]
    channel_rows = [r for r in rows if r["scope"] == "channel"]
    head = fit.headline(rows)
    bar_rows = _bar_rows(rows)
    blocked = sum(1 for row in ctx.store.quarantine(batch_id)
                  if row["code"] == "L_NO_PRICE_VARIATION") if batch_id else 0
    return web.render(
        request, "elasticity.html",
        rows=rows,
        sku_rows=sku_rows,
        channel_rows=channel_rows,
        head=head,
        bar_rows=bar_rows,
        blocked_groups=blocked,
        zero_mode=ctx.settings.zero_mode,
    )


def _bar_rows(rows) -> list[dict]:
    """把弹性画成 CSS 柱图：取绝对值最大的若干组，柱高按最大值归一。"""
    candidates = [r for r in rows if r["scope"] == "sku"]
    if not candidates:
        return []
    magnitudes = [abs(float(r["slope"])) for r in candidates]
    peak = max(magnitudes) or 1.0
    out = []
    for row in sorted(candidates, key=lambda r: r["group_key"]):
        value = abs(float(row["slope"]))
        out.append({
            "label": row["group_key"],
            "slope": row["slope"],
            "r2": row["r2"],
            "tvalue": row["tvalue"],
            "note": row["note"],
            "height": round(8 + 88 * value / peak, 1),
            "significant": bool(row["significant"]),
        })
    return out


__all__ = ["router", "service", "fit"]
