"""Private host-to-cluster transport; never expose the HBase write API over TCP."""
import os
from pathlib import Path
import stat

from paths import workspace


def socket_path() -> Path:
    return workspace().data / 'cluster-access' / 'hbase.sock'


def prepare_access() -> None:
    directory = socket_path().parent
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError('Cluster access directory must be owned by the current user')
    directory.chmod(0o700)


if __name__ == '__main__':
    prepare_access()
