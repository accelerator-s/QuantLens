import importlib
from exceptions import ModelRouteError

def ensure_module(module_name, *, pip_package=None):
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ModelRouteError(code="AI_DEPENDENCY_MISSING", message=f"缺少依赖：{pip_package or module_name}",
            suggested_action="在运行环境中安装依赖后重启界面") from exc
