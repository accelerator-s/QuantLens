from fastapi import APIRouter

from lifecycle.model_reviews import generate_review, list_reviews, review_evidence

from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=['model-reviews'])


def _require(kind, name):
    if kind == 'factor':
        from factors.registry import FactorRegistry
        FactorRegistry().get(name)
    else:
        from strategy.research import require_strategy
        require_strategy(name)


def _list(kind, name):
    _require(kind, name)
    task = manager.latest(resource=f'model-review:{kind}:{name}')
    return json_safe({'items': [{k: v for k, v in item.items() if k != 'evidence'}
                               for item in list_reviews(kind, name)],
                      'task': {key: task.get(key) for key in ('task_id', 'status', 'error')} if task else None})


def _generate(kind, name):
    _require(kind, name)
    evidence, preferred = review_evidence(kind, name)

    def run(report):
        report('读取已完成的结果，生成模型评价')
        record = generate_review(kind, name, evidence=evidence, model_name=preferred)
        report('模型评价已保存到评价记录')
        return {'review_id': record['review_id']}

    return {'task_id': manager.start('生成模型评价', run,
                resource=f'model-review:{kind}:{name}', category='research')}


@router.get('/factors/{name}/reviews')
def factor_reviews(name: str):
    return _list('factor', name)


@router.post('/factors/{name}/reviews')
def generate_factor_review(name: str):
    return _generate('factor', name)


@router.get('/strategies/{name}/reviews')
def strategy_reviews(name: str):
    return _list('strategy', name)


@router.post('/strategies/{name}/reviews')
def generate_strategy_review(name: str):
    return _generate('strategy', name)
