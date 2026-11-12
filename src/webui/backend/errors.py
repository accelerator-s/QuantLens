from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from exceptions import (
    ContractViolation,
    IssueNotFoundError,
    IssueStateError,
    ModelRouteError,
    QuantTrainingError,
)

_CODE_SUFFIX_STATUS = {
    "_NOT_FOUND": 404,
    "_CONFIRM_REQUIRED": 409,
}

def _status_for(exc: QuantTrainingError) -> int:
    
    if isinstance(exc, IssueNotFoundError):
        return 404
    if isinstance(exc, IssueStateError):
        return 409
    if isinstance(exc, ContractViolation):
        return 422
    if isinstance(exc, ModelRouteError):
        return 502
    code = exc.code.upper()
    for suffix, status in _CODE_SUFFIX_STATUS.items():
        if code.endswith(suffix):
            return status
    if "NOT_FOUND" in code:
        return 404
    return 400

def install_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(RequestValidationError)
    def _validation(request: Request,exc: RequestValidationError):
        labels={'folds':'年度数量','first_test_year':'首个评估年','train_years':'训练年数','name':'名称',
            'start':'开始日期','train_end':'训练截止','valid_end':'验证截止','end':'评估截止',
            'stock_limit':'股票数量上限','cores':'计算核数','trees':'树数量','depth':'树深度',
            'top_n':'持股数','cost_bps':'单边成本','as_of':'日期','run_id':'模型','daily_time':'运行时间',
            'account_id':'账户编号','alias':'别名','instrument_id':'证券代码','qty':'数量','price':'价格',
            'max_price':'最高价格','min_amount':'最低成交额','min_momentum':'最低涨幅',
            'max_position':'最高价格位置','min_roe':'最低净资产收益率','max_debt':'最高负债率',
            'question':'问题','thesis':'关注依据','actor':'操作者','yaml_text':'导入内容'}
        messages={'missing':'不能为空','int_parsing':'须填写整数','float_parsing':'须填写数字',
            'bool_parsing':'须选择是或否','string_type':'须填写文字','date_from_datetime_parsing':'日期格式不正确',
            'date_parsing':'日期格式不正确','extra_forbidden':'不支持此字段','literal_error':'不支持此选项',
            'string_too_short':'内容过短','string_too_long':'内容过长','greater_than':'须大于 {gt}',
            'greater_than_equal':'不得小于 {ge}','less_than':'须小于 {lt}','less_than_equal':'不得大于 {le}'}
        issues=[]
        for error in exc.errors():
            field=labels.get(str(error['loc'][-1]),'输入项')
            message=messages.get(error['type'],'内容不符合要求').format(**error.get('ctx',{}))
            if error['type']=='value_error':
                reason=str(error.get('ctx',{}).get('error',''))
                if any('\u4e00'<=c<='\u9fff' for c in reason):message=reason
            issues.append({'field':field,'message':message})
        detail={'code':'QL_INVALID_INPUT','message':'；'.join(f"{e['field']}：{e['message']}" for e in issues),
                'suggested_action':'请修改后重试','fields':issues}

        return JSONResponse(status_code=422,content={'detail':detail})

    @app.exception_handler(Exception)
    def _unexpected(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": unexpected_error(exc).to_dict()})

    @app.exception_handler(QuantTrainingError)
    def _quant_error_handler(request: Request, exc: QuantTrainingError) -> JSONResponse:
        payload = exc.to_dict()
        payload["module"] = type(exc).__name__
        return JSONResponse(status_code=_status_for(exc), content={"detail": payload})

def unexpected_error(exc):
    import logging
    from uuid import uuid4
    incident = uuid4().hex[:12]
    logging.getLogger("quantlens").exception("故障 %s", incident, exc_info=exc)
    return QuantTrainingError(code="QL_INTERNAL_ERROR",message=f"操作失败，故障编号 {incident}",
        suggested_action="查看任务日志；修复后重试",context={"incident":incident,"type":type(exc).__name__})
