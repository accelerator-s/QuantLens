from paths import workspace
from ai.secrets import load_into_env


def start_services(reporter=print):
    from quantlens.compute import ensure_cluster
    from quantlens.gpu import ensure_worker

    def phase(text, percent):
        reporter(text)
        if hasattr(reporter, 'progress'):
            reporter.progress({'phase': text, 'step': text, 'state': 'running', 'percent': percent})

    phase('启动计算集群', 5)
    ensure_cluster(reporter)
    phase('启动训练服务', 90)
    ensure_worker()
    phase('集群与训练服务已就绪', 100)


def prepare():
    for path in workspace().all_dirs():
        path.mkdir(parents=True, exist_ok=True)
    load_into_env()
    from portfolio.transaction import recover
    recover()
    from portfolio.accounts import AccountRegistry
    AccountRegistry().ensure_default()
    from quantlens.store import connect
    with connect() as db:
        db.execute('SELECT 1')
