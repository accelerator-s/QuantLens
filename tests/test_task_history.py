"""Task history stays bounded, and progress belongs to the owning workflow."""
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from webui.backend.server import create_app
from webui.backend.tasks import TaskManager


@pytest.fixture
def task_api(tmp_path):
    manager = object.__new__(TaskManager)
    manager._lock = threading.Lock()
    manager._db_path = tmp_path / 'tasks.sqlite'
    manager._init_db()
    manager._tasks = {
        f"task-{i:03}": {
            "task_id": f"task-{i:03}", "name": f"任务 {i}", "status": "completed",
            "created_at": "2026-11-14T10:00:00Z", "finished_at": None,
            "category": "data_sync", "resource": "data", "progress": {"percent": 100},
            "log": ["实际运行日志"], "error": "", "result": {"new_rows": i},
        } for i in range(45)
    }
    for record in manager._tasks.values():
        manager._persist(record)
    with patch('quantlens.routes.manager', manager), \
         patch('webui.backend.routes.data.manager', manager), \
         patch('webui.backend.routes.factor_chat.manager', manager), \
         patch('quantlens.ads.provenance', return_value={}), \
         patch('webui.backend.services.start_background_services'), \
         patch('webui.backend.server.AutomationScheduler.start'), \
         patch('webui.backend.server.AutomationScheduler.stop'), \
         patch('webui.backend.server.TrashCleanup.start'), \
         patch('webui.backend.server.ResearchResultPublisher.start'):
        with TestClient(create_app()) as client:
            yield client, manager


def test_history_cursor_survives_new_tasks_and_deleted_boundary(task_api):
    client, manager = task_api
    first = client.get('/api/jobs?limit=20').json()
    assert len(first['items']) == 20
    assert first['next_cursor']
    assert all('log' not in row and 'result' not in row for row in first['items'])
    manager._tasks['new'] = {**manager._tasks['task-044'], 'task_id': 'new',
                             'created_at': '2026-11-14T11:00:00Z'}
    manager._persist(manager._tasks['new'])
    with manager._connect() as conn:
        conn.execute('DELETE FROM webui_tasks WHERE task_id = ?', (first['items'][-1]['task_id'],))
    manager._tasks.clear()
    second = client.get('/api/jobs', params={'limit': 20, 'cursor': first['next_cursor']}).json()
    last = client.get('/api/jobs', params={'limit': 20, 'cursor': second['next_cursor']}).json()
    ids = [row['task_id'] for page in (first, second, last) for row in page['items']]
    assert ids == [f'task-{i:03}' for i in reversed(range(45))]
    assert last['next_cursor'] is None
    assert client.get('/api/jobs?limit=20').json()['items'][0]['task_id'] == 'new'


@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 101}, {'cursor': '!invalid!'}, {'cursor': 'bnVsbA=='}])
def test_history_rejects_invalid_page_requests(task_api, params):
    client, _ = task_api
    assert client.get('/api/jobs', params=params).status_code == 422


def test_progress_endpoints_are_scoped_and_old_detail_route_is_gone(task_api):
    client, manager = task_api
    manager._tasks['candidate'] = {**manager._tasks['task-001'], 'task_id': 'candidate',
                                    'resource': 'factor-ai-research', 'category': 'research'}
    manager._tasks['startup'] = {**manager._tasks['task-001'], 'task_id': 'startup',
                                  'resource': 'services', 'category': 'service_startup'}
    manager._persist(manager._tasks['candidate'])
    assert client.get('/api/tasks/task-001').status_code == 404
    sync = client.get('/api/data/sync/task-001/progress').json()
    assert sync['log'] == ['实际运行日志'] and sync['result'] == {'new_rows': 1}
    assert client.get('/api/data/sync/candidate/progress').status_code == 404
    assert client.get('/api/factors/chat/research/candidate/progress').json()['task_id'] == 'candidate'
    assert client.get('/api/factors/chat/research/startup/progress').status_code == 404
    assert client.get('/api/data/sync/startup/progress').status_code == 404
    candidates = client.get('/api/jobs', params={'resource': 'factor-ai-research', 'limit': 1}).json()
    assert [row['task_id'] for row in candidates['items']] == ['candidate']
