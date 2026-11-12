from __future__ import annotations

import importlib

from .exceptions import ProviderContractError, ProviderError
from .protocol import DataProvider
from .registry import ProviderRegistry

_PROVIDER_FACTORIES: dict[str, tuple[str, str]] = {
    "csmar": ("providers.markets.cn_a.csmar.adapter", "CSMARDataProvider"),
}

def get_provider(name: str) -> DataProvider:
    
    entry = _PROVIDER_FACTORIES.get(name)
    if entry is None:
        raise ProviderError(
            code="PROVIDER_NO_IMPL",
            message=f"数据源 {name} 无可用实现（未在 _PROVIDER_FACTORIES 中注册）",
            blocking=True,
        )

    module_path, class_name = entry
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise ProviderError(
            code="PROVIDER_IMPORT_FAIL",
            message=f"无法加载数据源实现 {module_path}.{class_name}：{exc}",
            blocking=True,
        ) from exc

    cls = getattr(mod, class_name, None)
    if cls is None:
        raise ProviderError(
            code="PROVIDER_CLASS_NOT_FOUND",
            message=f"模块 {module_path} 中未找到类 {class_name}",
            blocking=True,
        )

    dp: DataProvider = cls()

    meta = ProviderRegistry().get(name)
    if dp.market != meta.market or dp.instrument_class != meta.instrument_class:
        raise ProviderContractError(
            code="PROVIDER_MARKET_MISMATCH",
            message=(
                f"数据源 {name} 适配器声明 market={dp.market!r}, "
                f"instrument_class={dp.instrument_class!r}，"
                f"但注册表中为 market={meta.market!r}, "
                f"instrument_class={meta.instrument_class!r}"
            ),
            blocking=True,
        )

    return dp
