"""应用装配：配置 → 存储 → 模板 → 七个功能模块路由。

用工厂函数而不是模块级 app，测试可以给每个用例一个临时库。
"""
from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from . import config, errors, web
from .store import Store
from .modules.m1_ingest import handler as m1_handler
from .modules.m2_price import handler as m2_handler
from .modules.m3_quality import handler as m3_handler
from .modules.m4_elastic import handler as m4_handler
from .modules.m5_lift import handler as m5_handler
from .modules.m6_report import handler as m6_handler
from .modules.m7_design import handler as m7_handler

MODULES = (m1_handler, m2_handler, m3_handler, m4_handler, m5_handler, m6_handler,
           m7_handler)
PAGE_PATHS = ("/", "/contract", "/quarantine", "/elasticity", "/lift", "/report",
              "/design")


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.Settings.from_env()
    app = FastAPI(
        title="pricelift 促销价格弹性与增量测算",
        description="采信 → 归价 → 质量 → 弹性 → 增量 → 报告 → 实验设计",
        version=settings.rule_version,
    )
    app.state.settings = settings
    app.state.store = Store(settings.db_path)
    app.state.ctx = web.Ctx(settings=settings, store=app.state.store)
    web.setup(app)
    app.mount("/static", StaticFiles(directory=str(web.STATIC_DIR)), name="static")
    for module in MODULES:
        app.include_router(module.router)
    errors.install_handlers(app)

    def _head_ok() -> Response:
        return Response(status_code=200, media_type="text/html; charset=utf-8")

    for path in PAGE_PATHS + ("/healthz",):
        app.add_api_route(path, _head_ok, methods=["HEAD"], include_in_schema=False)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok", "rule_version": settings.rule_version,
                "zero_mode": settings.zero_mode,
                "sample_dir": str(settings.sample_dir)}

    return app
