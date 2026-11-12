from paths import workspace
from ai.secrets import load_into_env

def prepare():
    for path in workspace().all_dirs():
        path.mkdir(parents=True, exist_ok=True)
    load_into_env()
    from portfolio.transaction import recover
    recover()
    from quantlens.store import connect
    with connect() as db:
        db.execute('SELECT 1')
