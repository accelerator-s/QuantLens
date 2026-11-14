from __future__ import annotations

import logging
import threading

from paths import Workspace, workspace
from trash import TrashStore, synchronized

TRASH_RETENTION_DAYS = 30


class TrashRetention:
    def __init__(self, ws: Workspace | None = None):
        self.ws = ws or workspace()

    @synchronized
    def cleanup(self) -> list[str]:
        return TrashStore(self.ws).purge_older_than(TRASH_RETENTION_DAYS, confirm=True)

class TrashCleanup:
    """Enforce retention while the server runs, independently of daily automation."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="quantlens-trash-cleanup", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            try:
                TrashRetention().cleanup()
            except Exception:
                logging.getLogger(__name__).exception("回收站自动清理失败")
            self._stop.wait(60)
