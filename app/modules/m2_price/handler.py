"""M2 入口：列契约、换算表与单条试算。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ... import config, reasons, web
from . import coerce, contract

router = APIRouter()


class PriceIn(BaseModel):
    price: str = Field(..., description="含税单价原文，允许千分位与单位后缀")
    unit: str = Field("元/件", description="单价单位")
    tax: str = Field("0.13", description="税率原文")


@router.get("/api/contract")
def api_contract() -> dict:
    payload = contract.contract()
    payload["reasons"] = reasons.catalog()
    return payload


def _dump(model) -> dict:
    dump = getattr(model, "model_dump", None) or getattr(model, "dict")
    return dump()


@router.post("/api/price/preview")
def api_price_preview(body: PriceIn) -> dict:
    price_incl, notes = coerce.to_price_per_piece(body.price, body.unit)
    rate, tax_note = coerce.to_tax_rate(body.tax)
    if price_incl is None:
        return {"ok": False, "code": "P_SENTINEL_PRICE", "notes": notes,
                "input": _dump(body)}
    price_excl = coerce.excl_of(price_incl, rate)
    return {
        "ok": True,
        "input": _dump(body),
        "price_incl": str(price_incl),
        "price_excl": str(price_excl),
        "tax_rate": str(rate),
        "price_unit": config.PRICE_UNIT_BASE,
        "notes": notes + ([tax_note] if tax_note else []),
        "sentinel": coerce.is_sentinel(body.price),
    }


@router.get("/contract")
def page_contract(request: Request):
    ctx = web.get_ctx(request)
    batch = ctx.current_batch(request.query_params.get("batch_id"))
    batch_id = batch["batch_id"] if batch else None
    windows = ctx.store.promo_windows(batch_id) if batch_id else []
    promos = ctx.store.promos(batch_id) if batch_id else []
    return web.render(
        request, "contract.html",
        contract=contract.contract(),
        promos=promos,
        windows=windows,
        datasets=config.DATASETS,
    )
