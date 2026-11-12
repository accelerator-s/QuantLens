from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from exceptions import ContractViolation

class ConfirmPayload(BaseModel):

    model_config = ConfigDict(extra="forbid")

    confirm: bool = False

class DecidePayload(ConfirmPayload):

    decided_by: str = ""
    note: str = ""

class TransitionPayload(ConfirmPayload):

    target: str
    note: str = ""

class DecideTransitionPayload(DecidePayload):

    target: str

def confirm_preview(preview: dict) -> dict:
    
    return {"requires_confirm": True, "preview": preview}

def parse_enum[E: Enum](enum_cls: type[E], value: str, field: str) -> E:
    
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = "、".join(str(m.value) for m in enum_cls)
        raise ContractViolation(
            code="WEBUI_INVALID_ENUM_VALUE",
            message=f"参数 {field} 的取值 {value!r} 非法。",
            suggested_action=f"可选值：{allowed}。",
        ) from exc
