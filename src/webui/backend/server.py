from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from urllib.parse import urlparse

from version import __version__

from .errors import install_error_handlers
from .routes import api_router
from .tasks import manager
from automation import AutomationScheduler

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

def create_app() -> FastAPI:
    app = FastAPI(title="QuantLens", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1","localhost","testserver"])
    install_error_handlers(app)

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET","HEAD","OPTIONS"} and origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail":{"code":"QL_ORIGIN_DENIED","message":"请求来源不匹配","suggested_action":"从本机界面操作"}},status_code=403)
        return await call_next(request)
    app.include_router(api_router)
    scheduler = AutomationScheduler(manager)
    from quantlens.tracking import TrackingScheduler
    tracking=TrackingScheduler(manager)

    @app.on_event("startup")
    def start_scheduler() -> None:
        if os.environ.get('QUANTLENS_RECOVERY')!='1':
            scheduler.start()
            tracking.start()

    @app.on_event("shutdown")
    def stop_scheduler() -> None:
        if os.environ.get('QUANTLENS_RECOVERY')!='1':
            scheduler.stop()
            tracking.stop()

    @app.middleware("http")
    async def disable_frontend_cache(request: Request, call_next):
        
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
    return app
