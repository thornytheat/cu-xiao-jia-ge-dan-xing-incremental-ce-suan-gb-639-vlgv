"""M3 入口：原因码分布、隔离行筛选、隔离区 CSV、隔离页。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ... import reasons, web
from . import quarantine

router = APIRouter()


def _batch_id(request: Request) -> str | None:
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    return batch["batch_id"] if batch else None


@router.get("/api/quarantine")
def api_quarantine(request: Request) -> dict:
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    if batch_id is None:
        return {"batch_id": None, "rows": [], "distribution": [], "counts": {},
                "levels": reasons.LEVEL_LABELS}
    params = request.query_params
    rows = quarantine.enrich(ctx.store.quarantine(batch_id))
    filtered = quarantine.filter_rows(rows, level=params.get("level") or None,
                                      stage=params.get("stage") or None,
                                      code=params.get("code") or None)
    return {
        "batch_id": batch_id,
        "rows": filtered[:400],
        "total": len(filtered),
        "distribution": quarantine.distribution(rows),
        "counts": quarantine.level_counts(rows),
        "levels": reasons.LEVEL_LABELS,
    }


@router.get("/api/quarantine.csv")
def api_quarantine_csv(request: Request) -> Response:
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    if batch_id is None:
        body = quarantine.to_csv([])
    else:
        body = quarantine.to_csv(quarantine.enrich(ctx.store.quarantine(batch_id)))
    filename = f"pricelift-quarantine-{batch_id or 'empty'}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/quarantine")
def page_quarantine(request: Request):
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    rows = quarantine.enrich(ctx.store.quarantine(batch_id)) if batch_id else []
    return web.render(
        request, "quarantine.html",
        rows=rows[:400],
        total=len(rows),
        distribution=quarantine.distribution(rows),
        counts=quarantine.level_counts(rows),
    )
