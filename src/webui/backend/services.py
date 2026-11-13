"""Start backend dependencies as an observable, non-blocking WebUI task."""
import threading

from quantlens.bootstrap import start_services
from .tasks import manager

_lock = threading.Lock()
_CATEGORY = 'service_startup'


def start_background_services():
    with _lock:
        active = manager.latest(category=_CATEGORY, running_only=True)
        if active:
            return active['task_id']
        return manager.start('启动后台服务', start_services, resource='services', category=_CATEGORY)


def startup_status(*, include_logs=False):
    task = manager.latest(category=_CATEGORY)
    if not task:
        return None
    result = {key: task[key] for key in ('task_id', 'status', 'error')}
    result['phase'] = task['progress'].get('phase') or task['progress'].get('step', '')
    result['percent'] = task['progress'].get('percent', 0)
    if include_logs:
        result['log'] = task['log']
    return result
