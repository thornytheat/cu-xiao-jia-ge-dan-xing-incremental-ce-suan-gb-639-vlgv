"""M5 入口：增量测算触发、渠道分摊、批次历史与增量页。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ... import web
from . import service

router = APIRouter()


class LiftIn(BaseModel):
    dataset: str = Field("main", description="样例集键名")
    zero_mode: str = Field("", description="0 销量处理方式：shift / drop")


def _batch_id(request: Request) -> str | None:
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    return batch["batch_id"] if batch else None


@router.post("/api/lift")
def api_lift(body: LiftIn, request: Request) -> dict:
    settings = request.app.state.settings
    store = request.app.state.store
    result = service.run(settings=settings, store=store,
                         dataset=body.dataset or "main",
                         zero_mode=body.zero_mode or None)
    batch_id = result["batch"]["batch_id"]
    return {
        "status": result["batch"]["status"],
        "batch_id": batch_id,
        "run_id": result["batch"]["run_id"],
        "batch_key": result["batch_key"],
        "replay": result["replay"],
        "counters": result["counters"],
        "lift": store.lift(batch_id),
        "spread": store.spread(batch_id),
        "note": result["note"],
    }


@router.get("/api/lift")
def api_lift_state(request: Request) -> dict:
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    if batch_id is None:
        return {"batch_id": None, "lift": [], "spread": [], "totals": {}}
    return {
        "batch_id": batch_id,
        "lift": ctx.store.lift(batch_id),
        "spread": ctx.store.spread(batch_id),
        "totals": ctx.store.totals(batch_id),
    }


@router.get("/api/batches")
def api_batches(request: Request) -> dict:
    ctx = web.get_ctx(request)
    batches = ctx.store.list_batches(30)
    return {
        "count": len(batches),
        "batches": batches,
        "keys": [b["batch_key"][:12] for b in batches],
    }


@router.get("/lift")
def page_lift(request: Request):
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    lift_rows = ctx.store.lift(batch_id) if batch_id else []
    spread_rows = ctx.store.spread(batch_id) if batch_id else []
    totals = ctx.store.totals(batch_id) if batch_id else {}
    grouped: dict[str, list[dict]] = {}
    for row in spread_rows:
        grouped.setdefault(row["promo_id"], []).append(row)
    return web.render(
        request, "lift.html",
        lift=lift_rows,
        spread=grouped,
        totals=totals,
        batches=ctx.store.list_batches(12),
    )
