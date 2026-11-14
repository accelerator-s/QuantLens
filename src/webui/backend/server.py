from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .security import SecurityMiddleware

from version import __version__

from .errors import install_error_handlers
from .routes import api_router
from .tasks import manager
from automation import AutomationScheduler
from trash.retention import TrashCleanup
from quantlens.research_result_publisher import ResearchResultPublisher

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

def create_app() -> FastAPI:
    app = FastAPI(title="QuantLens", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1","localhost","testserver"])
    install_error_handlers(app)

    app.add_middleware(SecurityMiddleware)
    app.include_router(api_router)
    scheduler = AutomationScheduler(manager)
    from benchmarks.scheduler import BenchmarkDataScheduler
    benchmark_scheduler = BenchmarkDataScheduler(manager)
    trash_cleanup = TrashCleanup()
    research_publisher = ResearchResultPublisher(manager)
    from strategy.capital_guard import CapitalMonitor
    capital_monitor = CapitalMonitor()

    @app.on_event("startup")
    def start_scheduler() -> None:
        if os.environ.get('QUANTLENS_RECOVERY')!='1':
            from .services import start_background_services
            start_background_services()
            scheduler.start()
            benchmark_scheduler.start()
            trash_cleanup.start()
            research_publisher.start()
            capital_monitor.start()

    @app.on_event("shutdown")
    def stop_scheduler() -> None:
        if os.environ.get('QUANTLENS_RECOVERY')!='1':
            scheduler.stop()
            benchmark_scheduler.stop()
            trash_cleanup.stop()
            research_publisher.stop()
            capital_monitor.stop()

    @app.middleware("http")
    async def disable_frontend_cache(request: Request, call_next):
        
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    async def unknown_api(scope, receive, send):
        await JSONResponse({'detail': 'Not Found'}, status_code=404)(scope, receive, send)

    app.mount('/api', unknown_api, name='unknown-api')
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
    return app
