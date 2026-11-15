"""领域异常 → HTTP 映射；业务代码只抛这些，不直接构造 HTTPException。"""
from __future__ import annotations

from . import reasons


class PriceliftError(Exception):
    status_code = 400
    code = "E_BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None,
                 detail: dict | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if code:
            self.code = code
        if status_code is not None:
            self.status_code = status_code

    def payload(self) -> dict:
        return {
            "error": self.code,
            "message": self.message,
            "level": reasons.level_of(self.code),
            "hint": reasons.get(self.code).hint,
            "detail": self.detail,
        }


class BadRequest(PriceliftError):
    status_code = 400
    code = "E_BAD_REQUEST"


class NotFound(PriceliftError):
    status_code = 404
    code = "E_NOT_FOUND"


class Conflict(PriceliftError):
    status_code = 409
    code = "E_CONFLICT"


class Unprocessable(PriceliftError):
    """文件在结构上不可用（缺列 / 截断 / 空态）：语义问题是 422，不是 500。"""

    status_code = 422
    code = "E_STRUCTURE"


def install_handlers(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(PriceliftError)
    async def _pricelift(request: Request, exc: PriceliftError):  # noqa: ANN001
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):  # noqa: ANN001
        return JSONResponse(status_code=400, content={
            "error": "E_BAD_REQUEST", "message": str(exc), "level": reasons.ERROR,
            "hint": "检查参数名与取值范围", "detail": {}})
