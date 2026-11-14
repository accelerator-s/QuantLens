import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


class TaskCategories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        cls.environment=patch.dict(os.environ,QUANTLENS_HOME=cls.temporary.name)
        cls.environment.start()
        from webui.backend.tasks import TaskManager
        cls.manager=object.__new__(TaskManager)
        cls.manager._lock=threading.Lock()

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop();cls.temporary.cleanup()

    def test_sync_lookup_ignores_research_using_same_warehouse_lock(self):
        self.manager._tasks={
            'sync':{'task_id':'sync','name':'data-sync:csmar','resource':'data','status':'running','created_at':'2026-11-13T01:00:00Z','log':[],'progress':{}},
            'research':{'task_id':'research','name':'factor-evolution:EVO-test','resource':'data','status':'running','created_at':'2026-11-13T02:00:00Z','log':[],'progress':{}}}
        self.assertEqual(self.manager.latest(category='data_sync',running_only=True)['task_id'],'sync')
        self.assertEqual(self.manager.latest(category='research',running_only=True)['task_id'],'research')
        self.assertEqual(self.manager.latest(resource='data',running_only=True)['task_id'],'research')
        del self.manager._tasks['sync']
        self.assertIsNone(self.manager.latest(category='data_sync',running_only=True))

    def test_new_category_and_legacy_task_names_are_both_supported(self):
        from webui.backend.tasks import task_category
        self.assertEqual(task_category({'name':'data-sync:csmar'}),'data_sync')
        self.assertEqual(task_category({'name':'重建研究缓存'}),'research')
        self.assertEqual(task_category({'name':'custom','category':'data_sync'}),'data_sync')

    def test_downloaded_but_unpublished_data_needs_publication(self):
        from webui.backend.routes.data_ops import _publication_state
        with patch('quantlens.ads.manifest',return_value={'tables':{'overview':{'data_as_of':'2026-11-11','data_snapshot':'old'}}}):
            self.assertTrue(_publication_state('2026-11-12')['publication_pending'])
        with patch('quantlens.ads.manifest',return_value={'tables':{'overview':{'data_as_of':'2026-11-12','data_snapshot':'new'}}}):
            self.assertFalse(_publication_state('2026-11-12')['publication_pending'])


if __name__=='__main__':unittest.main()
