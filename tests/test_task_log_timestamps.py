"""Task logs keep their event times through persistence and task transitions."""
import importlib
import json
import time
from datetime import UTC, datetime
from unittest.mock import patch

import pytest


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    return importlib.import_module('webui.backend.tasks')


def record(task_id='task-log', status='completed'):
    return {
        'task_id': task_id, 'name': '日志检查', 'status': status, 'resource': 'test',
        'created_at': '2026-11-14T09:00:00Z', 'finished_at': None,
        'log': [], 'progress': {}, 'error': '', 'result': None,
    }


def wait_done(manager, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = manager.get(task_id)
        if task['finished_at']:
            return task
        time.sleep(.01)
    pytest.fail('Task did not finish')


def assert_timestamped(lines):
    assert lines
    for entry in lines:
        for line in entry.splitlines():
            timestamp, message = line.split(']  ', 1)
            parsed = datetime.fromisoformat(timestamp.removeprefix('['))
            assert parsed.utcoffset().total_seconds() == 8 * 3600
            assert message.strip()


def test_multiline_logs_are_timestamped_when_written_and_survive_reload(tasks):
    manager = tasks.TaskManager()
    item = record()
    reporter = tasks.TaskReporter(manager, item)
    with patch.object(tasks, 'datetime', wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 11, 14, 10, 3, 4, tzinfo=UTC)
        reporter('运行中\r\n  处理窗口\n\n  窗口完成')
    expected = (
        '[2026-11-14 18:03:04+08:00]  运行中\n'
        '[2026-11-14 18:03:04+08:00]    处理窗口\n'
        '[2026-11-14 18:03:04+08:00]    窗口完成'
    )
    assert item['log'] == [expected]
    with manager._connect() as conn:
        payload = conn.execute('SELECT payload FROM webui_tasks').fetchone()[0]
    assert json.loads(payload)['log'] == [expected]
    assert tasks.TaskManager().get(item['task_id'])['log'] == [expected]


def test_upstream_timestamps_are_preserved_without_duplicate_prefixes(tasks):
    item = record()
    tasks._append_log(item, '[2026-11-14 17:58:00+08:00]  原始事件\n  详情')
    assert item['log'] == [
        '[2026-11-14 17:58:00+08:00]  原始事件\n'
        '[2026-11-14 17:58:00+08:00]    详情'
    ]
    tasks._append_log(item, '2026-11-14T09:59:00Z  外部事件')
    assert item['log'][-1] == '2026-11-14T09:59:00Z  外部事件'


@pytest.mark.parametrize('unexpected', [False, True])
def test_failure_messages_have_persisted_timestamps(tasks, unexpected):
    manager = tasks.TaskManager()

    def fail(reporter):
        reporter('开始执行')
        if unexpected:
            raise RuntimeError('unexpected failure')
        raise tasks.QuantTrainingError(code='TEST_FAILURE', message='失败', suggested_action='检查配置')

    task_id = manager.start('失败检查', fail)
    task = wait_done(manager, task_id)
    assert task['status'] == 'failed'
    assert len(task['log']) == 2
    assert_timestamped(task['log'])
    assert tasks.TaskManager().get(task_id)['log'] == task['log']


def test_restart_timestamps_new_notice_without_fabricating_legacy_times(tasks):
    manager = tasks.TaskManager()
    item = record(status='running')
    item['log'] = ['历史日志没有时间']
    manager._persist(item)
    task = tasks.TaskManager().get(item['task_id'])
    assert task['status'] == 'interrupted'
    assert task['log'][0] == '历史日志没有时间'
    assert_timestamped(task['log'][1:])
    assert tasks.TaskManager().get(item['task_id'])['log'] == task['log']


def test_manual_start_notice_has_a_timestamp(tasks):
    manager = tasks.TaskManager()
    item = record()
    item['result'] = {'approval_status': 'pending'}
    manager._tasks[item['task_id']] = item
    manager._persist(item)
    result = manager.approve_result(item['task_id'], resource='test', start=lambda _result: {'task_id': 'started'})
    assert result['approval_status'] == 'approved'
    assert_timestamped(manager.get(item['task_id'])['log'])
    assert tasks.TaskManager().get(item['task_id'])['log'] == item['log']
