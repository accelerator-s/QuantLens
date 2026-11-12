import json
import threading
from datetime import datetime,date
from zoneinfo import ZoneInfo

from pydantic import BaseModel,Field,field_validator

from exceptions import ContractViolation,QuantTrainingError
from paths import workspace
from quantlens import store,data
from quantlens.model_ops import infer,observe,drift,forecast_mode


class TrackingConfig(BaseModel):
    enabled:bool=False
    run_id:str=''
    daily_time:str='18:30'
    sync_data:bool=True

    @field_validator('daily_time')
    @classmethod
    def valid_time(cls,value):
        datetime.strptime(value,'%H:%M')
        return value


def settings():
    path=workspace().config/'tracking.json'
    return TrackingConfig(**json.loads(path.read_text())) if path.exists() else TrackingConfig()


def configure(config):
    if config.run_id and not (store.run_path(config.run_id)/'metrics.json').exists():
        raise ContractViolation(code='QL_TRACKING_MODEL_INCOMPLETE',message='请选择已经完成评估的模型',suggested_action='先完成模型实验')
    if config.enabled and not config.run_id:
        raise ContractViolation(code='QL_TRACKING_MODEL_REQUIRED',message='启用跟踪前需要选择模型',suggested_action='从已完成实验中选择')
    store.write_json(workspace().config/'tracking.json',config.model_dump())
    store.add('tracking_config',config.model_dump())
    return config.model_dump()


def cycle(config,reporter):
    if not config.run_id:
        raise ContractViolation(code='QL_TRACKING_MODEL_REQUIRED',message='尚未选择跟踪模型',suggested_action='先保存跟踪设置')
    if config.sync_data:
        from quantlens.sync import run_sync
        from webui.backend.routes.data_ops import _active_provider,_log_console
        reporter('同步数据并重建缓存')
        run_sync(_active_provider(),console=_log_console(reporter),on_progress=reporter.progress)
    end=data.coverage()['end'][:10]
    existing=next((r for r in store.records('forecast',5000) if r['body']['run_id']==config.run_id and r['body']['as_of']==end),None)
    forecast=existing or infer(config.run_id,date.fromisoformat(end),reporter)
    reporter('补齐到期收益并更新跟踪统计')
    outcomes=observe()
    result={'run_id':config.run_id,'forecast_id':forecast['id'],'as_of':end,'mode':forecast_mode(date.fromisoformat(forecast['body']['as_of']),datetime.fromisoformat(forecast['body']['created_at'])),
            'reused':existing is not None,'observed':len(outcomes['items']),'drift':drift(config.run_id)}
    return store.add('tracking_result',result)


class TrackingScheduler:
    def __init__(self,manager):
        self.manager=manager
        self.stop_event=threading.Event()
        self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.loop,name='QuantLens-tracking',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def tick(self):
        config=settings()
        now=datetime.now(ZoneInfo('Asia/Shanghai'))
        if not config.enabled or now.weekday()>4 or now.strftime('%H:%M')<config.daily_time:return
        day=now.date().isoformat()
        if any(r['body']['day']==day for r in store.records('tracking_attempt',100)):return
        if self.manager.latest(resource='data',running_only=True):return
        task=self.manager.start('每日模型跟踪',lambda r:cycle(config,r),resource='data')
        store.add('tracking_attempt',{'day':day,'task_id':task,'config':config.model_dump()})

    def loop(self):
        while not self.stop_event.wait(30):
            try:self.tick()
            except Exception as exc:
                from webui.backend.errors import unexpected_error
                error=exc if isinstance(exc,QuantTrainingError) else unexpected_error(exc)
                store.add('tracking_error',{'code':error.code,'message':error.message})
                self.stop_event.wait(300)
