from __future__ import annotations
import hashlib,json
from datetime import date
from typing import Literal
from pydantic import BaseModel,ConfigDict,field_validator,model_validator

class EvaluationConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    factor_name:str
    start:str
    end:str
    return_field:Literal['ret_wd']='ret_wd'
    primary_horizon:Literal[1,5,10,20]=5

    @field_validator('start','end')
    @classmethod
    def _date(cls,value):
        date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def _window(self):
        if self.start>self.end:raise ValueError('评价日期顺序错误')
        return self

    def content_hash(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()[:16]

__all__=['EvaluationConfig']
