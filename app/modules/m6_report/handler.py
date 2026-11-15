"""M6 入口：报告、四账、CSV 导出与运行清单。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ... import config, web
from . import service

router = APIRouter()

KINDS = ("lift", "sales", "elastic", "reconcile")


def _batch_id(request: Request) -> str | None:
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    return batch["batch_id"] if batch else None


@router.get("/api/report")
def api_report(request: Request) -> dict:
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    if batch_id is None:
        return {"batch_id": None, "reconcile": [], "balanced": True, "totals": {},
                "lift": [], "distribution": []}
    payload = service.report_payload(ctx.store, batch_id)
    payload["manifest"] = ctx.store.manifest(batch_id)
    return payload


@router.get("/api/export")
def api_export(request: Request) -> Response:
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    kind = (request.query_params.get("kind") or "lift").lower()
    fmt = (request.query_params.get("fmt") or "csv").lower()
    if batch_id is None:
        return Response(content="batch_id 缺失：先跑一次测算".encode("utf-8"),
                        status_code=422, media_type="text/plain; charset=utf-8")
    if fmt in ("json", "csv") and kind == "manifest":
        name, body = service.export_manifest(ctx.store, batch_id, fmt)
    else:
        if kind not in KINDS:
            kind = "lift"
        body = service.export_rows(kind, ctx.store, batch_id)
        name = service.export_filename(kind, batch_id)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/report")
def page_report(request: Request):
    ctx = web.get_ctx(request)
    batch_id = _batch_id(request)
    if batch_id is None:
        return web.render(request, "report.html", payload=None, kinds=KINDS,
                          money_unit=config.MONEY_UNIT, qty_unit=config.QTY_UNIT)
    payload = service.report_payload(ctx.store, batch_id)
    return web.render(
        request, "report.html",
        payload=payload,
        kinds=KINDS,
        manifest=ctx.store.manifest(batch_id),
        money_unit=config.MONEY_UNIT,
        qty_unit=config.QTY_UNIT,
    )
