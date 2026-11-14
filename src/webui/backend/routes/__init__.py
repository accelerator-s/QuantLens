from __future__ import annotations

from fastapi import APIRouter

from . import (
    admin,
    ai_config,
    backtest,
    chat_history,
    data,
    data_ops,
    experiments,
    factor_chat,
    factor_eval,
    factor_evolution,
    factors,
    lifecycle,
    operations,
    portfolio,
    portfolio_write,
    recommend,
    status,
    strategy,
    strategy_ideas,
    market_rules,
    model_reviews,
    trash,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(status.router)
api_router.include_router(model_reviews.router)
api_router.include_router(chat_history.router)
api_router.include_router(ai_config.router)
api_router.include_router(factors.router)
api_router.include_router(factor_chat.router)
api_router.include_router(factor_eval.router)
api_router.include_router(strategy_ideas.router)
api_router.include_router(strategy.router)
api_router.include_router(market_rules.router)
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
api_router.include_router(trash.router)
api_router.include_router(admin.router)

from quantlens.routes import router as lens_router
api_router.include_router(lens_router)
