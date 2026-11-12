"""CSMAR download-page protocol, exercised without network requests."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from providers.markets.cn_a.csmar.client import CSMARClient
from providers.exceptions import ProviderNetworkError, ProviderDataError
import websocket


class PackagingProtocol(unittest.TestCase):
    def client(self):
        events = []
        client = CSMARClient(on_progress=events.append)
        client.get_outline = Mock(return_value={'estimateTime': 1})
        client._find_record_file_path = Mock(return_value=None)
        client.cancel = Mock()
        return client, events

    def test_start_and_forward_real_percentage(self):
        client, events = self.client()
        socket = Mock()
        socket.recv.side_effect = [json.dumps({'code': 0, 'data': data}) for data in [
            {'percentage': 0, 'status': '3'},
            {'percentage': '86%', 'status': '0'},
            {'percentage': 100, 'filePath': 'download/data.zip'},
        ]]
        with patch('websocket.create_connection', return_value=socket):
            self.assertEqual(client._wait_pack_result('outline-test'), 'download/data.zip')
        sent = json.loads(socket.send.call_args.args[0])
        self.assertEqual((sent['outlineId'], sent['status']), ('outline-test', 'start'))
        self.assertEqual([e['pack_percent'] for e in events], [None, 0, 86, 100])
        self.assertEqual(events[1]['pack_stage'], 'queued')
        self.assertEqual(events[-1]['pack_stage'], 'ready')
        socket.close.assert_called_once()
        client.cancel.assert_not_called()

    def test_lost_connection_checks_existing_record_without_repack(self):
        client, events = self.client()
        client._find_record_file_path.return_value = 'existing.zip'
        with patch('websocket.create_connection', side_effect=websocket.WebSocketConnectionClosedException()):
            self.assertEqual(client._wait_pack_result('id'), 'existing.zip')
        self.assertEqual(events[-1]['pack_percent'], 100)
        client.cancel.assert_not_called()

    def test_connection_failure_is_reported_without_resubmission(self):
        client, _ = self.client()
        with patch('websocket.create_connection', side_effect=OSError()), self.assertRaises(ProviderNetworkError) as ctx:
            client._wait_pack_result('id')
        self.assertEqual(ctx.exception.code, 'PROVIDER_PACK_CONNECTION_LOST')
        client.cancel.assert_not_called()

    def test_empty_result_closes_socket(self):
        client, _ = self.client()
        socket = Mock()
        socket.recv.return_value = json.dumps({'code': -5501})
        with patch('websocket.create_connection', return_value=socket), self.assertRaises(ProviderDataError) as ctx:
            client._wait_pack_result('id')
        self.assertEqual(ctx.exception.code, 'PROVIDER_EMPTY_RESULT')
        socket.close.assert_called_once()

    def test_outline_carries_official_table_estimate(self):
        client, _ = self.client()
        client._client = Mock()
        client._client.get.return_value.json.return_value = {'code': 0, 'data': {'estimateTime': 3}}
        client._client.post.return_value.json.return_value = {'code': 0, 'data': 'outline'}
        outline = client._save_outline(code_str='', field_str='58,59', start_date='2026-11-05',
                                       end_date='2026-11-11', table_name='daily',
                                       table_name_phy='TRD_Dalyr', table_id=4, frequency='0')
        self.assertEqual(outline, 'outline')
        self.assertEqual(client._client.post.call_args.kwargs['json']['estimateTime'], 3)

    def test_existing_package_does_not_start_another_socket(self):
        client, events = self.client()
        client._token = 'test-session'
        client._save_outline = Mock(return_value='outline')
        client._pack = Mock(return_value='record')
        client._record_download_path = Mock(return_value='ready.zip')
        client._download_file = Mock(return_value=b'archive')
        client._parse_zip = Mock(return_value='parsed')
        client._wait_pack_result = Mock()
        self.assertEqual(client._download([], '2026-11-05', '2026-11-11'), 'parsed')
        client._wait_pack_result.assert_not_called()
        self.assertEqual([e['pack_stage'] for e in events], ['ready', 'transferring', 'parsing'])


if __name__ == '__main__':
    unittest.main()
