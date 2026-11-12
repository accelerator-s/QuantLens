import json
import shutil
import threading
import uuid
from contextlib import contextmanager

from paths import workspace

lock=threading.RLock()


def rollback(directory):
    manifest=json.loads((directory/'state.json').read_text())
    for item in manifest['files']:
        target=workspace().root/item['path']
        if item['existed']:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(directory/item['copy'],target)
        else:target.unlink(missing_ok=True)


def recover():
    root=workspace().portfolio_root/'.transactions'
    for state in root.glob('*/state.json'):
        item=json.loads(state.read_text())
        if item['status']=='prepared':
            rollback(state.parent)
            state.write_text(json.dumps({**item,'status':'rolled_back'}))


@contextmanager
def transaction(files):
    root=workspace().portfolio_root/'.transactions'/uuid.uuid4().hex
    root.mkdir(parents=True)
    entries=[]
    for index,path in enumerate(files):
        name=f'{index}.bin'
        if path.exists():shutil.copyfile(path,root/name)
        entries.append({'path':str(path.relative_to(workspace().root)),'existed':path.exists(),'copy':name})
    state={'status':'prepared','files':entries}
    (root/'state.json').write_text(json.dumps(state))
    try:
        yield
        temp=root/'committed.tmp'
        temp.write_text(json.dumps({**state,'status':'committed'}))
        temp.replace(root/'state.json')
    except Exception:
        rollback(root)
        (root/'state.json').write_text(json.dumps({**state,'status':'rolled_back'}))
        raise
