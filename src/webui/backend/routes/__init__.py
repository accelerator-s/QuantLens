from __future__ import annotations

from fastapi import APIRouter

from . import (
    admin,
    ai_config,
    backtest,
    data,
    data_ops,
    experiments,
    factor_chat,
    factor_eval,
    factor_evolution,
    factors,
    issues,
    lifecycle,
    operations,
    portfolio,
    portfolio_write,
    recommend,
    review,
    status,
    strategy,
    strategy_chat,
    strategy_evolution,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(status.router)
api_router.include_router(issues.router)
api_router.include_router(ai_config.router)
api_router.include_router(factors.router)
api_router.include_router(factor_chat.router)
api_router.include_router(factor_eval.router)
api_router.include_router(strategy.router)
api_router.include_router(strategy_chat.router)
api_router.include_router(strategy_evolution.router)
api_router.include_router(data.router)
api_router.include_router(data_ops.router)
api_router.include_router(backtest.router)
api_router.include_router(experiments.router)
api_router.include_router(lifecycle.router)
api_router.include_router(operations.router)
api_router.include_router(factor_evolution.router)
api_router.include_router(portfolio.router)
api_router.include_router(portfolio_write.router)
api_router.include_router(recommend.router)
api_router.include_router(review.router)
api_router.include_router(admin.router)

from quantlens.routes import router as lens_router
api_router.include_router(lens_router)
