"""M1 入口：导入并跑通全流程、样例集清单、总览页。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ... import config, web
from ..m5_lift import service as lift_service
from . import service


class ImportIn(BaseModel):
    dataset: str = Field("main", description="样例集键名")
    zero_mode: str = Field("", description="0 销量处理方式：shift / drop")


router = APIRouter()


@router.get("/api/datasets")
def api_datasets(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "datasets": service.describe(settings),
        "state_labels": service.STATE_LABELS,
        "zero_modes": list(config.ZERO_MODES),
        "current": settings.zero_mode,
    }


@router.post("/api/import")
def api_import(body: ImportIn, request: Request) -> dict:
    settings = request.app.state.settings
    store = request.app.state.store
    result = lift_service.run(
        settings=settings, store=store,
        dataset=body.dataset or "main",
        zero_mode=body.zero_mode or None)
    batch = result["batch"]
    return {
        "status": batch["status"],
        "batch_id": batch["batch_id"],
        "run_id": batch["run_id"],
        "batch_key": result["batch_key"],
        "replay": result["replay"],
        "dataset": batch["dataset"],
        "counters": result["counters"],
        "note": result["note"],
    }


@router.get("/api/import")
def api_import_state(request: Request) -> dict:
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    if batch is None:
        return {"batch": None, "sources": [], "stages": [], "counters": {}}
    return {
        "batch": batch,
        "sources": ctx.store.sources(batch["batch_id"]),
        "stages": ctx.store.stages(batch["batch_id"]),
        "counters": ctx.store.totals(batch["batch_id"]),
    }


@router.get("/")
def page_index(request: Request):
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    batch_id = batch["batch_id"] if batch else None
    return web.render(
        request, "index.html",
        sources=ctx.store.sources(batch_id) if batch_id else [],
        totals=ctx.store.totals(batch_id) if batch_id else {},
        batches=ctx.store.list_batches(10),
        datasets=service.describe(ctx.settings),
        state_labels=service.STATE_LABELS,
    )
