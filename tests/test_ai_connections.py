import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai.registry import AIModel, ModelKind, ModelRegistry
from ai.transport import completion, discover_models, endpoint_base, probe_model
from exceptions import ContractViolation, ModelRouteError


def model(api_type='openai'):
    return AIModel(name='test', kind=ModelKind.API_MODEL, provider_endpoint='https://models.test', api_model_id='test-model', api_type=api_type, capability_tags=['structured_output'])


def client(handler):
    return patch('ai.transport.create_http_client', side_effect=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler)))


class Connections(unittest.TestCase):
    def test_old_registry_defaults_to_openai_and_protocol_persists(self):
        old = model().model_dump(mode='json'); old.pop('api_type')
        self.assertEqual(AIModel.model_validate(old).api_type, 'openai')
        with tempfile.TemporaryDirectory() as temp:
            registry = ModelRegistry(Path(temp) / 'models.yaml')
            registry.add(model('anthropic'))
            self.assertEqual(registry.get('test').api_type, 'anthropic')

    def test_url_normalization(self):
        self.assertEqual(endpoint_base('https://models.test/'), 'https://models.test/v1')
        self.assertEqual(endpoint_base('https://models.test/proxy/v1/messages'), 'https://models.test/proxy/v1')
        for invalid in ['file:///etc/passwd', 'https://user:secret@models.test', 'https://models.test?key=secret']:
            with self.assertRaises(ModelRouteError): endpoint_base(invalid)

    def test_openai_chat_and_probe_latency(self):
        def handler(request):
            self.assertEqual(request.url.path, '/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-secret')
            body = json.loads(request.content)
            self.assertEqual(body['model'], 'test-model')
            return httpx.Response(200, json={'choices': [{'message': {'role':'assistant', 'content':'OK'}}]})
        with client(handler):
            result = probe_model(model(), 'test-secret')
        self.assertTrue(result['ok']); self.assertGreaterEqual(result['latency_ms'], 0)
        self.assertEqual(result['reply'], 'OK')

    def test_anthropic_tool_roundtrip_preserves_blocks(self):
        blocks = [{'type':'thinking', 'thinking':'thought', 'signature':'signature'}, {'type':'tool_use', 'id':'call-1', 'name':'lookup', 'input':{'q':'test'}}]
        calls = []
        def handler(request):
            self.assertEqual(request.url.path, '/v1/messages')
            self.assertEqual(request.headers['x-api-key'], 'test-secret')
            self.assertEqual(request.headers['anthropic-version'], '2023-06-01')
            body = json.loads(request.content); calls.append(body)
            self.assertNotIn('response_format', body)
            if len(calls) == 1:
                self.assertEqual(body['system'], 'system')
                self.assertEqual(body['tools'][0]['input_schema'], {'type':'object'})
                return httpx.Response(200, json={'content':blocks})
            self.assertEqual(body['messages'][1]['content'], blocks)
            self.assertEqual(body['messages'][2]['content'][0], {'type':'tool_result','tool_use_id':'call-1','content':'result'})
            return httpx.Response(200, json={'content':[{'type':'text','text':'{"ok":true}'}]})
        messages = [{'role':'system','content':'system'}, {'role':'user','content':'question'}]
        tools = [{'type':'function','function':{'name':'lookup','parameters':{'type':'object'}}}]
        with client(handler):
            reply = completion(model('anthropic'), 'test-secret', messages, tools=tools)
            messages.extend([reply, {'role':'tool','tool_call_id':'call-1','content':'result'}])
            final = completion(model('anthropic'), 'test-secret', messages, tools=tools, tool_choice='none', json_output=True)
        self.assertEqual(final['content'], '{"ok":true}')

    def test_model_discovery_both_protocols_and_pagination(self):
        for protocol in ['openai','anthropic']:
            count = []
            def handler(request):
                self.assertEqual(request.url.path, '/v1/models')
                self.assertIn('x-api-key' if protocol == 'anthropic' else 'authorization', request.headers)
                count.append(1)
                if len(count) == 1: return httpx.Response(200,json={'data':[{'id':'b'}],'has_more':True,'last_id':'b'})
                self.assertEqual(request.url.params['after_id'], 'b')
                return httpx.Response(200,json={'data':[{'id':'a','display_name':'Model A'},{'id':'b'}],'has_more':False})
            with client(handler): self.assertEqual([x['id'] for x in discover_models('https://models.test', protocol, 'test-secret')], ['a','b'])

    def test_errors_do_not_expose_gateway_key(self):
        for response in [httpx.Response(401,text='test-secret'), httpx.Response(200,text='test-secret')]:
            with client(lambda _: response), self.assertRaises(ModelRouteError) as raised:
                completion(model(), 'test-secret', [{'role':'user','content':'test'}])
            self.assertNotIn('test-secret', str(raised.exception.to_dict()))

    def test_reusing_saved_key_requires_same_endpoint_and_protocol(self):
        from webui.backend.routes import ai_config as routes
        saved = model()
        with patch.object(routes, 'ModelRegistry') as registry, patch.object(routes, '_api_key_for', return_value='stored') as key:
            registry.return_value.get.return_value = saved
            payload = routes.ConnectionPayload(provider_endpoint='https://models.test/v1',existing_name='test')
            self.assertEqual(routes._connection_key(payload), 'stored')
            for changes in [{'provider_endpoint':'https://other.test'}, {'api_type':'anthropic'}]:
                with self.assertRaises(ContractViolation): routes._connection_key(payload.model_copy(update=changes))
            key.assert_called_once()

    def test_unsaved_discovery_and_probe_do_not_persist_credentials(self):
        from webui.backend.routes import ai_config as routes
        payload = routes.ConnectionPayload(provider_endpoint='https://models.test',api_type='anthropic',api_key='test-secret',api_model_id='test-model')
        with patch.object(routes.secrets, 'set_secret') as save, patch.object(routes, 'ModelRegistry') as registry, patch.object(routes, 'discover_models', return_value=[{'id':'a'}]), patch.object(routes, 'probe_model',return_value={'latency_ms':15}):
            self.assertEqual(routes.fetch_connection_models(payload)['items'], [{'id':'a'}])
            self.assertEqual(routes.test_connection(payload)['latency_ms'], 15)
            save.assert_not_called();registry.assert_not_called()

    def test_system_hosts_mapping_bypasses_environment_proxy(self):
        from net import create_http_client
        with tempfile.TemporaryDirectory() as temp:
            hosts = Path(temp) / 'hosts'; hosts.write_text('127.0.0.1 api.internal alias.internal # comment\n')
            with patch('net._system_hosts_path', return_value=hosts), patch('net.resolve_proxy_url', return_value='http://127.0.0.1:9999'), create_http_client(target_url='https://api.internal/v1') as connection:
                direct = connection._transport_for_url(httpx.URL('https://api.internal/v1'))
                proxy = connection._transport_for_url(httpx.URL('https://remote.test/v1'))
                self.assertNotEqual(type(direct._pool),type(proxy._pool))


if __name__ == '__main__': unittest.main()
